"""Latency work: the fast search must return exactly what the reference
(score everything, plain sort) returns, its caches must follow every change,
and they must never widen what anyone can see."""
import gc
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402
from brain_speed import _filler  # noqa: E402
from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain import retrieve as R  # noqa: E402
from puzzle_assistant.brain.query import make_entities  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import Answer, Question  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402


def _flat(hits):
    return [(h.module, h.record_id, h.score, h.status, h.established, h.text, h.sources,
             json.dumps(h.detail, sort_keys=True, ensure_ascii=False, default=str))
            for h in hits]


class FastEqualsReferenceTests(unittest.TestCase):
    """Every realistic question, both search paths, with filler claims and the
    quiz memory / World Atlas adapters: fast results == reference results."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(setattr, R, "EXHAUSTIVE", False)

    def _compare(self, b, questions, users):
        n = 0
        for q, _, _, asker in questions:
            for fts in (True, False):
                for rej in (False, True):
                    R.EXHAUSTIVE = True
                    ref = _flat(b.search(q, viewer=users[asker], use_fts=fts,
                                         include_rejected=rej))
                    R.EXHAUSTIVE = False
                    fast = _flat(b.search(q, viewer=users[asker], use_fts=fts,
                                          include_rejected=rej))
                    self.assertEqual(fast, ref, (q, asker, fts, rej))
                    n += 1
        return n

    def test_villa_brisa_with_filler(self):
        b, ids, users = E1.build(os.path.join(self.dir, "a.db"))
        _filler(b, 600)
        self.addCleanup(b.close)
        self.assertEqual(self._compare(b, E1.QUESTIONS + E1.HELDOUT, users), 63 * 4)

    def test_ciudad_coral_with_filler(self):
        b, ids, users = E2.build(os.path.join(self.dir, "b.db"))
        _filler(b, 600)
        self.addCleanup(b.close)
        self.assertEqual(self._compare(b, E2.QUESTIONS, users), 47 * 4)

    def test_with_quiz_memory_and_world_atlas(self):
        qpath, apath = os.path.join(self.dir, "habbosk.db"), os.path.join(self.dir, "atlas.db")
        m = Memory(qpath)
        m.save(Question("¿QUIÉN SOY?", "Soy un asistente NPC", ["Rubén", "Mateo", "Leo"]),
               Answer(choice="Leo", explanation="Leo cubre el turno del gimnasio.",
                      source="game", verified=True))
        m.close()
        a = Atlas(apath)
        a.import_input()
        a.record_visit(13, "Ayuntamiento de Ensenada", None, None, "start")
        a.close()
        b = Brain(os.path.join(self.dir, "k.db"), qpath, apath)
        self.addCleanup(b.close)
        b.learn("Leo trabaja en el gimnasio.", "user", "Leo")
        users = {"ana": b.local_user}
        qs = [(q, "", [], "ana") for q in ("asistente NPC Leo gimnasio", "Gran Casino",
                                           "Ayuntamiento de Ensenada room ID", "¿Quién es Leo?")]
        self.assertEqual(self._compare(b, qs, users), 16)


class CacheFollowsChangesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "k.db")
        self.b = Brain(self.path, "", "")
        self.addCleanup(self.b.close)
        st = self.b.store
        self.luis = st.add_contributor("Luis", "external_user")
        self.mod = st.add_contributor("Mod", "moderator")

    def first(self, q, viewer=None):
        hits = self.b.search(q, viewer=viewer)
        return (hits[0].text, hits[0].status, hits[0].detail.get("scope")) if hits else None

    def test_every_kind_of_change_is_seen_by_the_next_search(self):
        q = "room ID of the Demo Garden"
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.assertEqual(self.first(q)[1], "submitted")                  # cached now
        self.b.review(c, "awaiting_verification")
        self.assertEqual(self.first(q)[1], "awaiting_verification")
        self.b.correct(c, "The Demo Garden has room ID 900002.")
        self.assertEqual(self.first(q)[0], "The Demo Garden has room ID 900002.")
        other = self.b.learn("The Demo Garden has room ID 900003.", "user", "Demo Garden")
        self.b.link(other, c, "contradicts")
        self.assertEqual({h.status for h in self.b.search(q)}, {"conflicting"})
        self.b.review(other, "rejected")
        s = self.b.add_source("screenshot", title="s", reference="s://1")
        self.b.add_evidence(c, "direct", s)
        self.b.review(c, "verified")
        self.b.propose_share(c)
        self.assertEqual(self.first(q)[2], "shared_candidate")
        self.b.review(c, "verified", reviewer=self.mod)
        self.b.approve_share(c, self.mod)
        self.assertEqual(self.first(q), ("The Demo Garden has room ID 900002.", "verified",
                                         "shared"))
        self.assertEqual(self.first(q, viewer=self.luis)[2], "shared")   # now everyone's

    def test_changes_made_by_another_program_are_seen(self):
        q = "room ID of the Demo Garden"
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.first(q)
        other = Brain(self.path, "", "")                                  # a second program
        other.review(c, "outdated")
        other.learn("The Demo Garden has room ID 900009.", "user", "Demo Garden")
        other.close()
        hits = self.b.search(q)
        self.assertEqual([h.status for h in hits if "900001" in h.text], ["outdated"])
        self.assertTrue(any("900009" in h.text for h in hits))

    def test_cache_never_widens_what_someone_can_see(self):
        mine = self.b.learn("My locker code in the Demo Garden is 4411.", "user", "Demo Garden")
        self.assertTrue(self.b.search("locker code Demo Garden"))        # cached for Ana
        self.assertEqual(self.b.search("locker code Demo Garden", viewer=self.luis), [])
        self.assertEqual(self.b.store.index_rows(self.luis, [mine]), [])  # asked directly
        keys, _ = self.b.store.visible_keys(self.luis, ["locker"], None, ids_only=True)
        self.assertEqual(keys, [])

    def test_incremental_name_list_equals_a_full_rebuild(self):
        q = "room ID of the Demo Garden"
        self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.b.search(q)
        for name in ("Zeta Hall", "Alpha Court", "Demo Garden Annex", "Mid Plaza"):
            self.b.learn(f"The {name} has room ID 1.", "user", name)
            self.b.search(q)                                              # incremental
        with self.b.store._lock:
            cached = R._entities(self.b.store, R._cache(self.b.store), self.b.local_user, True, ())
        full = make_entities([(s, "library", n)
                              for s, n in self.b.store.visible_subjects(self.b.local_user) if s])
        self.assertEqual([(e.name, e.key) for e in cached], [(e.name, e.key) for e in full])


class ExternalChangeTests(unittest.TestCase):
    """Changes by ANY other connection - raw SQL, deletes, restores - and
    concurrent searches must never leave stale or someone else's data."""
    Q = "room ID of the Demo Garden"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "k.db")
        self.b = Brain(self.path, "", "")
        self.addCleanup(self.b.close)
        self.luis = self.b.store.add_contributor("Luis", "external_user")

    def raw(self, *statements):
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA foreign_keys=OFF")
        for sql, args in statements:
            con.execute(sql, args)
        con.commit()
        con.close()

    def test_raw_external_edit_is_seen(self):
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.b.search(self.Q)
        self.raw(("UPDATE claims SET text=?, status='outdated' WHERE id=?",
                  ("The Demo Garden has room ID 777777.", c)),
                 ("UPDATE claim_index SET norm=? WHERE claim_id=?",
                  ("demo garden the demo garden has room id 777777", c)))
        h = self.b.search(self.Q)
        self.assertEqual([(x.text, x.status) for x in h],
                         [("The Demo Garden has room ID 777777.", "outdated")])

    def test_external_delete_and_id_reuse_never_shows_someone_elses_text(self):
        c = self.b.learn("Luis secret: room 4471023 is my hideout.", "user", "Demo Garden",
                         by=self.luis)
        self.b.search("hideout room Demo Garden", viewer=self.luis)          # cached
        self.raw(("DELETE FROM claim_events WHERE claim_id=?", (c,)),
                 ("DELETE FROM claim_index WHERE claim_id=?", (c,)),
                 ("DELETE FROM claim_fts WHERE rowid=?", (c,)),
                 ("DELETE FROM claims WHERE id=?", (c,)))
        # no search in between: the new claim's event even reuses the deleted event's id
        mine = self.b.learn("Ana: the Demo Garden hideout room is public.", "user", "Demo Garden")
        self.assertEqual(mine, c)                                            # id reused
        texts = [h.text for h in self.b.search("hideout room Demo Garden")]
        self.assertEqual(texts, ["Ana: the Demo Garden hideout room is public."])
        self.assertNotIn("4471023", repr(texts))
        self.assertEqual(self.b.search("hideout room Demo Garden", viewer=self.luis), [])

    def test_external_change_can_only_narrow_access(self):
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.raw(("UPDATE claims SET scope='shared' WHERE id=?", (c,)))
        self.assertTrue(self.b.search(self.Q, viewer=self.luis))            # shared: seen
        self.raw(("UPDATE claims SET scope='personal' WHERE id=?", (c,)))
        self.assertEqual(self.b.search(self.Q, viewer=self.luis), [])       # withdrawn: gone

    def test_restored_older_file_is_seen(self):
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        bk = os.path.join(self.dir, "bk.db")
        src, dst = sqlite3.connect(self.path), sqlite3.connect(bk)
        src.backup(dst)
        dst.close()
        src.close()
        self.b.review(c, "outdated")
        self.b.learn("The Demo Garden has room ID 900002.", "user", "Demo Garden")
        self.assertEqual(len(self.b.search(self.Q)), 2)
        src, dst = sqlite3.connect(bk), sqlite3.connect(self.path)          # restore the copy
        src.backup(dst)
        dst.close()
        src.close()
        self.assertEqual([(h.text, h.status) for h in self.b.search(self.Q)],
                         [("The Demo Garden has room ID 900001.", "submitted")])

    def test_event_log_going_backwards_empties_the_cache(self):
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        self.b.search(self.Q)
        with self.b.store._lock:
            cache = R._cache(self.b.store)
            cache.mark += 1000                                               # "newer" than the file
            cache.docs[c].text = "stale"                                     # poison the entry
        self.assertEqual(self.b.search(self.Q)[0].text, "The Demo Garden has room ID 900001.")

    def test_concurrent_search_cannot_store_a_stale_row(self):
        import threading
        import time
        c = self.b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        orig = self.b.store.index_rows
        inside, go = threading.Event(), threading.Event()

        def slow(viewer, ids):
            rows = orig(viewer, ids)            # thread 1 has read the old row...
            inside.set()
            go.wait(5)                          # ...and pauses before storing it
            return rows
        self.b.store.index_rows = slow
        t1 = threading.Thread(target=lambda: self.b.search(self.Q))
        t1.start()
        self.assertTrue(inside.wait(5))
        self.b.store.index_rows = orig
        t2 = threading.Thread(target=lambda: (self.b.review(c, "outdated"),
                                              self.b.search(self.Q)))
        t2.start()
        time.sleep(0.3)
        go.set()
        t1.join(5)
        t2.join(5)
        self.assertEqual([h.status for h in self.b.search(self.Q)], ["outdated"])

    def test_cache_holds_at_most_one_entry_per_claim(self):
        for i in range(50):
            self.b.learn(f"The Demo Garden {i} has room ID {900000 + i}.", "user", f"Demo Garden {i}")
        for _ in range(20):
            for q in (self.Q, "Demo Garden 7 room ID", "nothing at all"):
                self.b.search(q)
                self.b.search(q, viewer=self.luis)
        with self.b.store._lock:
            cache = R._cache(self.b.store)
        self.assertLessEqual(len(cache.docs), 50)
        self.assertLessEqual(len(cache.names), 32)


class GcPauseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.b = Brain(os.path.join(self.dir, "k.db"), "", "")
        self.addCleanup(self.b.close)
        self.addCleanup(gc.enable)

    def test_collector_state_is_restored(self):
        gc.enable()
        self.b.search("anything")
        self.assertTrue(gc.isenabled())
        gc.disable()
        self.b.search("anything")
        self.assertFalse(gc.isenabled())                                 # left as it was
        gc.enable()
        with mock.patch.object(R, "_search", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.b.search("anything")
        self.assertTrue(gc.isenabled())                                  # even after an error


if __name__ == "__main__":
    unittest.main()
