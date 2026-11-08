"""B5 knowledge model: stored facts, relations as data, entities / aliases /
"same as" (never merged automatically), safe migration into a new file,
and the Brain never using the quiz memory or World Atlas as its database.
Synthetic data in temporary folders only."""
import hashlib
import itertools
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import brain_answer_eval as A  # noqa: E402
import brain_citation_set as C  # noqa: E402
import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402
from puzzle_assistant.brain import Brain, facts  # noqa: E402
from puzzle_assistant.brain.facts import extract  # noqa: E402
from puzzle_assistant.brain.migrate import migrate_file  # noqa: E402
from puzzle_assistant.brain.store import IMPORTER, BrainError, NeedsMigration  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sk_b5_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, name="k.db"):
        return os.path.join(self.dir, name)


def stored_vs_extracted(b):
    con = b.store._db
    out = []
    for cid, text, subject in con.execute("SELECT id, text, subject FROM claims"):
        row = b.store.stored_fact(cid)
        f = extract(text, subject or "")
        got = None if row["relation"] is None else (row["relation"], row["place"],
                                                    None if row["absent"] else row["value"],
                                                    row["qualifier"], row["display"])
        want = None if f is None else (f.relation, f.place, f.value, f.qualifier, f.display)
        if got != want:
            out.append((text, got, want))
    return out


class StoredFacts(Base):
    def test_stored_facts_equal_b3_extraction_on_every_dataset(self):
        for name, mod in (("brisa", E1), ("coral", E2), ("nube", A)):
            b, *_ = mod.build(self.path(name + ".db"))
            self.assertEqual(stored_vs_extracted(b), [], name)
            n = b.store._db.execute("SELECT COUNT(*) FROM claim_facts").fetchone()[0]
            self.assertEqual(n, b.store._db.execute("SELECT COUNT(*) FROM claims").fetchone()[0])
            b.close()

    def test_stored_facts_equal_extraction_on_fuzzed_sentences(self):
        rnd = random.Random(5)
        names = ["Mira", "Don Tito", "the garden", "Capitana Irene", "someone", "Sala Azul 2",
                 "Jardín de la Luna", "Plaza Mayor", "bruno", "Faro del Norte"]
        frames = ["{a} maintains the {b}.", "{a} is the caretaker of the {b}.", "Maybe {a} runs the {b}.",
                  "The {b} has room ID {n}.", "The {b} has no caretaker.", "{a} does not maintain the {b}.",
                  "The north door of the {b} leads to the {a}.", "El encargado del {b} es {a}.",
                  "La misión del {b} da {n} monedas.", "{a} runs the {b} and the shop.",
                  "{a} used to run the {b}.", "Nobody maintains the {b}."]
        b = Brain(self.path(), "", "")
        for i in range(400):
            t = rnd.choice(frames).format(a=rnd.choice(names), b=rnd.choice(names), n=rnd.randint(1, 999))
            b.learn(t, "user", rnd.choice(["", "Plaza Mayor", "Sala Azul"]))
        for t, s, q, l in C.ITEMS:
            b.learn(t, "user", s)
            if q:
                b.learn(q, "user", s)
        self.assertEqual(stored_vs_extracted(b), [])
        b.close()

    def test_corrections_and_outside_edits_keep_facts_right(self):
        b = Brain(self.path(), "", "")
        c = b.learn("Mira maintains the Jardín Nube.", "user", "Jardín Nube")
        b.correct(c, "Leo maintains the Jardín Nube.")
        self.assertEqual(b.store.stored_fact(c)["value"], "leo")
        self.assertEqual(b.ask("Who maintains the Jardín Nube?").possible, "Leo")
        b.close()
        con = sqlite3.connect(self.path())                 # another program edits the text
        con.execute("UPDATE claims SET text='Ana maintains the Jardín Nube.' WHERE id=?", (c,))
        con.commit()
        con.close()
        b = Brain(self.path(), "", "")                     # re-read at start
        self.assertEqual(b.store.stored_fact(c)["value"], "ana")
        self.assertEqual(b.ask("Who maintains the Jardín Nube?").possible, "Ana")
        b.close()

    def test_a_stale_stored_fact_is_never_used(self):
        b = Brain(self.path(), "", "")
        c = b.learn("Mira maintains the Jardín Nube.", "user", "Jardín Nube")
        b.search("x")
        b.store._db.execute("UPDATE claim_facts SET value='kai', display='Kai' WHERE claim_id=?", (c,))
        b.store._db.execute("UPDATE claims SET text='Leo maintains the Jardín Nube.' WHERE id=?", (c,))
        b.store._db.commit()
        from puzzle_assistant.brain.retrieve import forget_cache
        forget_cache(b.store)
        self.assertEqual(b.ask("Who maintains the Jardín Nube?").possible, "Leo")   # re-read
        b.close()

    def test_opening_and_first_answer_do_not_reread_every_claim(self):
        b, ids, users = A.build(self.path())
        for i in range(2000):
            b.store.add_claim(f"Filler {i} maintains the Filler Room {i}.", "user", subject=f"F {i}")
        b.close()
        calls = []
        real = facts._extract_b3
        with mock.patch.object(facts, "_extract_b3", lambda *a, **k: calls.append(1) or real(*a, **k)):
            t = time.perf_counter()
            b = Brain(self.path(), "", "")
            r = b.respond("Who maintains the Jardín Nube?", viewer=users["ana"])
            elapsed = time.perf_counter() - t
            b.close()
        self.assertEqual(r.value, "Mira")
        # facts came from claim_facts: only the answer's own quote is read (B4's
        # citation check), never the 2,000+ claims
        self.assertLessEqual(len(calls), 4)
        self.assertLess(elapsed, 5)


class RelationsAsData(Base):
    def setUp(self):
        super().setUp()
        self.b = Brain(self.path(), "", "")
        self.addCleanup(self.b.close)
        src = self.b.add_source("document", title="Hours")
        for text in ("The Glass Library opens at nine.", "The Clock Tower opens at noon.",
                     "Mira maintains the Glass Library."):
            c = self.b.learn(text, "imported")
            self.b.add_evidence(c, "direct", src, text)
            self.b.review(c, "verified")
        self.before = self.b.ask("Who maintains the Glass Library?")

    def add(self, **kw):
        args = dict(key="opens_at", value_type="text",
                    sentence_forms=["The {place} opens at {value}"],
                    question_forms=["when does the {place} open", "a qué hora abre la {place}"],
                    labels={"en": "when it opens", "es": "cuándo abre"})
        args.update(kw)
        self.b.add_relation(**args)

    def test_a_new_relation_needs_no_code(self):
        self.assertIn(self.b.respond("When does the Glass Library open?").category, ("unknown",))
        self.add()
        r = self.b.respond("When does the Glass Library open?")
        self.assertEqual((r.category, r.value), ("established", "nine"))
        r = self.b.respond("¿A qué hora abre la Clock Tower?")
        self.assertEqual((r.category, r.value), ("established", "noon"))
        self.assertIn("opens_at", [x["key"] for x in self.b.relations()])

    def test_b3_results_do_not_change(self):
        self.add()
        after = self.b.ask("Who maintains the Glass Library?")
        self.assertEqual((after.status, after.answer, after.trace),
                         (self.before.status, self.before.answer, self.before.trace))
        self.assertEqual(self.b.store.stored_fact(3)["relation"], "caretaker")   # B3 form won

    def test_uncertain_or_negative_claims_state_no_added_fact(self):
        self.add()
        for text in ("Maybe the Fish Market opens at ten.", "The Fish Market does not open at ten.",
                     "The Fish Market opens at ten and eleven.",
                     "The Fish Market opens at ten probably.", "The Fish Market opens at ten, I think.",
                     "The Fish Market opens at ten, not eleven."):
            c = self.b.learn(text, "user")
            self.assertIsNone(self.b.store.stored_fact(c)["relation"], text)

    def test_who_may_add_and_what_is_refused(self):
        luis = self.b.store.add_contributor("Luis")
        with self.assertRaises(BrainError):
            self.add(by=luis)
        with self.assertRaises(BrainError):
            self.add(by=IMPORTER)
        for bad in ({"key": "caretaker"}, {"key": "Bad Key"}, {"value_type": "colour"},
                    {"sentence_forms": ["no slots"]}, {"sentence_forms": []},
                    {"question_forms": ["when {value}"]}, {"cardinality": "few"}):
            with self.assertRaises(BrainError, msg=str(bad)):
                self.add(**bad)
        self.assertNotIn("opens_at", [x["key"] for x in self.b.relations()])


class EntitiesAndAliases(Base):
    def setUp(self):
        super().setUp()
        self.b = Brain(self.path(), "", "")
        self.addCleanup(self.b.close)
        st = self.b.store
        self.luis = st.add_contributor("Luis")
        self.mod = st.add_contributor("Mod", kind="moderator")
        src = self.b.add_source("document", title="Guide", by=self.mod)
        for text in ("The Faro Norte has room ID 700301.", "The Faro Sur has room ID 700302.",
                     "The Old Pier has room ID 700900."):        # shared: everyone may use them
            c = self.b.learn(text, "imported", by=self.mod)
            self.b.add_evidence(c, "direct", src, text, by=self.mod)
            self.b.review(c, "verified", reviewer=self.mod)
            self.b.propose_share(c, by=self.mod)
            self.b.review(c, "verified", reviewer=self.mod)
            self.b.approve_share(c, self.mod)

    def q(self, q, viewer=None):
        return self.b.respond(q, viewer=viewer)

    def test_learning_never_creates_or_merges_entities(self):
        for _ in range(3):
            self.b.learn("Mira maintains the Faro Norte.", "user")
        con = self.b.store._db
        self.assertEqual([con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("entities", "aliases", "entity_links")], [0, 0, 0])

    def test_an_approved_alias_reaches_the_same_place(self):
        self.assertEqual(self.q("What is the room ID of the Faro del Norte?").reason,
                         "nothing_documented")
        e = self.b.add_entity("Faro Norte", "room")
        self.b.add_alias(e, "Faro del Norte", "es")
        r = self.q("What is the room ID of the Faro del Norte?")
        self.assertEqual((r.category, r.value), ("established", "700301"))

    def test_proposed_aliases_are_not_used_until_a_person_approves(self):
        e = self.b.add_entity("Faro Norte")
        a = self.b.add_alias(e, "Faro del Norte", by=IMPORTER)
        self.assertEqual(self.q("What is the room ID of the Faro del Norte?").category, "unknown")
        with self.assertRaises(BrainError):
            self.b.approve_alias(a, by=IMPORTER)
        self.b.approve_alias(a)
        self.assertEqual(self.q("What is the room ID of the Faro del Norte?").value, "700301")

    def test_someone_elses_personal_alias_is_not_used(self):
        e = self.b.add_entity("Faro Norte", by=self.luis)
        self.b.add_alias(e, "Faro del Norte", by=self.luis)
        self.assertEqual(self.q("What is the room ID of the Faro del Norte?", self.luis).value,
                         "700301")
        self.assertEqual(self.q("What is the room ID of the Faro del Norte?").category, "unknown")

    def test_shared_aliases_are_made_by_moderators(self):
        with self.assertRaises(BrainError):
            self.b.add_entity("Faro Norte", scope="shared", by=self.luis)
        e = self.b.add_entity("Faro Norte", scope="shared", by=self.mod)
        with self.assertRaises(BrainError):
            self.b.add_alias(e, "Faro del Norte", by=self.luis)
        self.b.add_alias(e, "Faro del Norte", by=self.mod)
        for v in (None, self.luis):
            self.assertEqual(self.q("What is the room ID of the Faro del Norte?", v).value, "700301")

    def test_an_alias_standing_for_two_places_stays_ambiguous(self):
        n, s = self.b.add_entity("Faro Norte"), self.b.add_entity("Faro Sur")
        self.b.add_alias(n, "El Faro")
        self.b.add_alias(s, "El Faro")
        r = self.q("What is the room ID of El Faro?")
        self.assertEqual((r.category, r.reason), ("unknown", "ambiguous"))
        self.assertEqual(sorted(r.clarification["options"]), ["Faro Norte", "Faro Sur"])

    def test_a_fact_about_an_ambiguous_alias_is_never_moved_to_one_place(self):
        n, s = self.b.add_entity("Faro Norte"), self.b.add_entity("Faro Sur")
        self.b.add_alias(n, "Lighthouse")
        self.b.add_alias(s, "Lighthouse")
        self.b.learn("The Lighthouse has room ID 700999.", "user")
        r = self.q("What is the room ID of the Faro Norte?")
        self.assertEqual((r.category, r.value), ("established", "700301"))

    def test_alias_map_never_includes_another_persons_alias(self):
        shared = self.b.add_entity("Faro Norte", scope="shared", by=self.mod)
        con = self.b.store._db                # a stray row: Luis's personal alias of it
        con.execute("INSERT INTO aliases (entity_id, alias, key, status, scope, owner_id, added_by, "
                    "added_at) VALUES (?,?,?,?,?,?,?,?)",
                    (shared, "Luis Light", "luis light", "approved", "personal", self.luis,
                     self.luis, "t"))
        con.commit()
        self.assertIn("luis light", self.b.store.alias_map(self.luis))
        self.assertNotIn("luis light", self.b.store.alias_map(None))

    def test_automatic_contributors_cannot_link_or_approve_even_their_own(self):
        a, b = self.b.add_entity("X One", by=IMPORTER), self.b.add_entity("X Two", by=IMPORTER)
        with self.assertRaises(BrainError):
            self.b.link_entities(a, b, by=IMPORTER)
        al = self.b.add_alias(a, "X Uno", by=IMPORTER)
        with self.assertRaises(BrainError):
            self.b.approve_alias(al, by=IMPORTER)
        con = self.b.store._db
        self.assertEqual(con.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT status FROM aliases WHERE id=?", (al,)).fetchone()[0],
                         "proposed")

    def test_partial_names_stay_ambiguous_with_aliases_present(self):
        e = self.b.add_entity("Faro Norte")
        self.b.add_alias(e, "Faro del Norte")
        self.assertEqual(self.q("What is the room ID of the Faro?").reason, "ambiguous")

    def test_same_as_only_by_a_person_and_reversible(self):
        pier, muelle = self.b.add_entity("Old Pier"), self.b.add_entity("Muelle Viejo")
        self.assertEqual(self.q("What is the room ID of the Muelle Viejo?").category, "unknown")
        with self.assertRaises(BrainError):
            self.b.link_entities(pier, muelle, by=IMPORTER)
        link = self.b.link_entities(pier, muelle)
        self.assertEqual(self.q("What is the room ID of the Muelle Viejo?").value, "700900")
        self.b.unlink_entities(link)
        self.assertEqual(self.q("What is the room ID of the Muelle Viejo?").category, "unknown")
        row = self.b.store._db.execute("SELECT active, removed_by FROM entity_links").fetchone()
        self.assertEqual(row[0], 0)                        # kept as history
        self.assertTrue(row[1])

    def test_identical_names_are_never_merged(self):
        a, b = self.b.add_entity("Ana", "person"), self.b.add_entity("Ana", "person")
        self.assertNotEqual(a, b)
        self.assertEqual(len([e for e in self.b.entities() if e["name"] == "Ana"]), 2)
        self.assertEqual(self.b.store._db.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0], 0)

    def test_automatic_contributors_only_propose(self):
        e = self.b.add_entity("Faro Norte", by=IMPORTER)
        self.assertEqual(self.b.store._entity(e)["status"], "proposed")
        self.b.add_alias(self.b.add_entity("Faro Norte"), "Northern Light")
        # a proposed entity's aliases are never used either
        self.b.add_alias(e, "Faro N", by=IMPORTER)
        self.assertEqual(self.q("What is the room ID of the Faro N?").category, "unknown")


def _v3_file(path):
    """A real format-3 file: built now, then the B5 tables removed."""
    b = Brain(path, "", "")
    src = b.add_source("document", title="t")
    c = b.learn("Mira maintains the Faro Norte.", "user", "Faro Norte")
    b.add_evidence(c, "direct", src, "q")
    b.review(c, "verified")
    b.close()
    con = sqlite3.connect(path)
    for t in ("claim_facts", "relations", "entities", "aliases", "entity_links"):
        con.execute(f"DROP TABLE {t}")
    con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    con.execute("DELETE FROM meta WHERE key='facts_stamp'")
    con.commit()
    con.close()


def _fp(path):
    out = {}
    for s in ("", "-journal", "-wal", "-shm"):
        p = path + s
        out[s] = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
    return out


class Migration(Base):
    def test_an_older_file_is_refused_unchanged_and_migrated_into_a_new_file(self):
        old = self.path("knowledge.db")
        _v3_file(old)
        before, listing = _fp(old), sorted(os.listdir(self.dir))
        with self.assertRaises(NeedsMigration):
            Brain(old, "", "")
        self.assertEqual((_fp(old), sorted(os.listdir(self.dir))), (before, listing))
        rep = migrate_file(old, self.path("knowledge.v4.db"))
        self.assertTrue(rep["original_unchanged"])
        self.assertEqual(rep["rows"]["claims"], 1)
        self.assertEqual(_fp(old), before)
        b = Brain(self.path("knowledge.v4.db"), "", "")
        self.assertEqual(b.ask("Who maintains the Faro Norte?").answer, "Mira")
        b.close()

    def test_an_older_file_with_a_leftover_journal_is_refused_untouched(self):
        old = self.path("knowledge.db")
        _v3_file(old)
        crash = textwrap.dedent("""
            import os, sqlite3, sys
            con = sqlite3.connect(sys.argv[1], isolation_level=None)
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE claims SET text='HALF WRITE'")
            con.execute("PRAGMA cache_size=1")
            con.execute("CREATE TABLE filler(x)")
            con.executemany("INSERT INTO filler VALUES (?)", [("x" * 2000,)] * 400)
            os._exit(9)
        """)
        self.assertEqual(subprocess.run([sys.executable, "-c", crash, old]).returncode, 9)
        before, listing = _fp(old), sorted(os.listdir(self.dir))
        self.assertIsNotNone(before["-journal"])
        with self.assertRaises(NeedsMigration):
            Brain(old, "", "")
        self.assertEqual((_fp(old), sorted(os.listdir(self.dir))), (before, listing))

    def test_never_overwrites_and_never_the_same_file(self):
        old = self.path("knowledge.db")
        _v3_file(old)
        open(self.path("exists.db"), "w").close()
        for dst in (self.path("exists.db"), old):
            with self.assertRaises(BrainError):
                migrate_file(old, dst)
        self.assertEqual(os.path.getsize(self.path("exists.db")), 0)

    def test_a_leftover_journal_is_recovered_in_the_copy_only(self):
        old = self.path("knowledge.db")
        _v3_file(old)
        crash = textwrap.dedent("""
            import os, sqlite3, sys
            con = sqlite3.connect(sys.argv[1], isolation_level=None)
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE claims SET text='CORRUPTED HALF WRITE'")
            con.execute("PRAGMA cache_size=1")
            con.execute("CREATE TABLE filler(x)")
            con.executemany("INSERT INTO filler VALUES (?)", [("x" * 2000,)] * 400)
            os._exit(9)
        """)
        r = subprocess.run([sys.executable, "-c", crash, old], capture_output=True)
        self.assertEqual(r.returncode, 9)
        self.assertTrue(os.path.exists(old + "-journal"))
        before = _fp(old)
        migrate_file(old, self.path("new.db"))
        self.assertEqual(_fp(old), before)                 # journal still there, untouched
        b = Brain(self.path("new.db"), "", "")
        self.assertEqual(b.claim(1)["text"], "Mira maintains the Faro Norte.")
        b.close()

    def test_failures_leave_no_new_file_and_the_original_untouched(self):
        old = self.path("knowledge.db")
        _v3_file(old)
        before = _fp(old)
        from puzzle_assistant.brain import migrate as M
        real = M.KnowledgeStore

        def tamper(path, _upgrade=False):                  # an upgrade that loses a row
            s = real(path, _upgrade=_upgrade)
            s._db.execute("DELETE FROM claim_events WHERE id=1")
            s._db.commit()
            return s
        with mock.patch.object(M, "KnowledgeStore", tamper):
            with self.assertRaises(BrainError):
                migrate_file(old, self.path("new.db"))
        real_copy = M.shutil.copyfile
        changed = []

        def changes_meanwhile(a, b):
            real_copy(a, b)
            if a == old and not changed:                   # another program writes, once
                changed.append(1)
                with open(old, "ab") as fh:
                    fh.write(b"\0")
        with mock.patch.object(M.shutil, "copyfile", changes_meanwhile):
            with self.assertRaisesRegex(BrainError, "while it was being copied"):
                migrate_file(old, self.path("new2.db"))
        with open(old, "rb+") as fh:                       # undo our own simulated change
            fh.truncate(os.path.getsize(old) - 1)
        self.assertEqual(_fp(old)[""], before[""])
        self.assertFalse(any(os.path.exists(self.path(n)) for n in ("new.db", "new2.db")))
        with open(self.path("junk.db"), "wb") as fh:
            fh.write(b"not a database" * 100)
        with self.assertRaises(BrainError):
            migrate_file(self.path("junk.db"), self.path("new3.db"))
        self.assertFalse(os.path.exists(self.path("new3.db")))

    def test_a_newer_file_is_refused(self):
        p = self.path()
        Brain(p, "", "").close()
        con = sqlite3.connect(p)
        con.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
        con.commit()
        con.close()
        with self.assertRaises(BrainError):
            migrate_file(p, self.path("new.db"))


class NeverTheQuizOrAtlasFile(Base):
    def test_refused_as_the_brains_database(self):
        for name in ("habbosk.db", "atlas.db", "HABBOSK.DB"):
            with self.assertRaises(BrainError):
                Brain(self.path(name), "", "")
            self.assertFalse(os.path.exists(self.path(name)))
        p = self.path("k.db")
        with self.assertRaises(BrainError):
            Brain(p, p, "")
        with self.assertRaises(BrainError):
            Brain(p, "", p)


if __name__ == "__main__":
    unittest.main()
