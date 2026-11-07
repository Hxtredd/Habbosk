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
        cached = R._entities(self.b.store, self.b.local_user, True, ())
        full = make_entities([(s, "library", n)
                              for s, n in self.b.store.visible_subjects(self.b.local_user) if s])
        self.assertEqual([(e.name, e.key) for e in cached], [(e.name, e.key) for e in full])


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
