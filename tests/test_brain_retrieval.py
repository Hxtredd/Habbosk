"""SK Brain B2 retrieval: the nine regression questions, synonyms, similar
names, conflicts, trust ranking, duplicates, privacy, FTS5 / fallback
equivalence, migration safety and speed.

The five claims are a reconstruction of the owner's B1 experiment (synthetic,
unverified); replace them with the exact originals when available."""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain import Brain  # noqa: E402

CLAIMS = [("The Demo Garden has room ID 9001.", "Demo Garden", "rooms"),
          ("The northern archway of the Main Plaza leads to the Demo Garden.", "northern archway",
           "navigation"),
          ("Gardener Lina maintains the fountains in the Demo Garden.", "fountains", "characters"),
          ("The east fountain in the Demo Garden is disabled.", "east fountain", "objects"),
          ("The Crystal Courtyard is next to the Demo Garden.", "Crystal Courtyard", "rooms")]

QUESTIONS = {
    1: "What is the room ID of the Demo Garden?",
    2: "How do I get to the Demo Garden?",
    3: "Where does the northern archway lead?",
    4: "Who maintains the fountains?",
    5: "Which fountain is disabled?",
    6: "What is the name of the caretaker of the Crystal Courtyard?",
    7: "What number identifies the Demo Garden?",
    8: "What is the capital of France?",
    9: "Who is the caretaker of the Crystal Courtyard?",
}


def tmp(name="k.db"):
    return os.path.join(tempfile.mkdtemp(), name)


def matches(hits):
    return [h for h in hits if h.detail["why"]["matches_question"]]


class Base(unittest.TestCase):
    def setUp(self):
        self.b = Brain(tmp(), "", "")
        self.ids = [self.b.learn(t, "user", s, c) for t, s, c in CLAIMS]

    def tearDown(self):
        self.b.close()

    def top(self, q, **kw):
        hits = self.b.search(q, **kw)
        return hits[0] if hits else None


class RegressionTests(Base):
    """Expected behaviour of the owner's nine questions."""

    def test_q1_room_id(self):
        h = self.top(QUESTIONS[1])
        self.assertEqual(h.record_id, str(self.ids[0]))
        self.assertTrue(h.detail["why"]["matches_question"])
        self.assertIn("Unverified", h.display())                 # never shown as fact

    def test_q2_route_within_top3(self):
        hits = self.b.search(QUESTIONS[2])[:3]
        self.assertIn(str(self.ids[1]), [h.record_id for h in hits])
        self.assertEqual(hits[0].record_id, str(self.ids[1]))

    def test_q3_northern_archway(self):
        self.assertEqual(self.top(QUESTIONS[3]).record_id, str(self.ids[1]))

    def test_q4_who_maintains(self):
        self.assertEqual(self.top(QUESTIONS[4]).record_id, str(self.ids[2]))

    def test_q5_disabled_fountain(self):
        self.assertEqual(self.top(QUESTIONS[5]).record_id, str(self.ids[3]))

    def test_q6_q9_no_invented_caretaker(self):
        for n in (6, 9):
            hits = self.b.search(QUESTIONS[n])
            self.assertEqual(matches(hits), [], n)             # nothing answers the question
            lina = [h for h in hits if h.record_id == str(self.ids[2])]
            for h in lina:                                     # another place's caretaker...
                self.assertIn("different subject: fountains", h.detail["why"]["notes"])
            self.assertEqual(hits[0].record_id, str(self.ids[4]))  # the courtyard claim itself

    def test_q7_number_is_the_id(self):
        h = self.top(QUESTIONS[7])
        self.assertEqual(h.record_id, str(self.ids[0]))
        self.assertTrue(h.detail["why"]["matches_question"])

    def test_q8_unrelated_returns_nothing(self):
        self.assertEqual(self.b.search(QUESTIONS[8]), [])
        self.assertFalse(self.b.ask(QUESTIONS[8]).answered)


class SynonymTests(Base):
    def test_synonyms_and_word_forms(self):
        cases = {"Who is the caretaker of the fountains?": 2,      # caretaker ~ maintains
                 "Who is the keeper of the fountain?": 2,
                 "What is the identifier of the Demo Garden?": 0,
                 "Which fountain is broken?": 3,                   # broken ~ disabled
                 "Where does the north arch go?": 1,               # north/arch/go
                 "¿Qué fuente está apagada?": 3,                   # Spanish
                 "¿Cuál es el número de la sala Demo Garden?": 0}
        for q, i in cases.items():
            h = self.top(q)
            self.assertIsNotNone(h, q)
            self.assertEqual(h.record_id, str(self.ids[i]), q)

    def test_synonym_counts_less_than_the_exact_word(self):
        exact = self.top("Who maintains the fountains?")
        syn = self.top("Who is the custodian of the fountains?")
        self.assertEqual(exact.record_id, syn.record_id)
        self.assertLess(syn.detail["why"]["relevance"], exact.detail["why"]["relevance"])


class SimilarNameTests(Base):
    def test_similar_room_names_are_not_confused(self):
        annex = self.b.learn("The Demo Garden Annex has room ID 9002.", "user", "Demo Garden Annex",
                             "rooms")
        h = self.top(QUESTIONS[1])
        self.assertEqual(h.record_id, str(self.ids[0]))
        other = [x for x in self.b.search(QUESTIONS[1]) if x.record_id == str(annex)]
        self.assertTrue(not other or not other[0].detail["why"]["matches_question"])
        h2 = self.top("What is the room ID of the Demo Garden Annex?")
        self.assertEqual(h2.record_id, str(annex))
        self.assertTrue(h2.detail["why"]["matches_question"])

    def test_partial_name_is_ambiguous_not_resolved(self):
        self.b.learn("The Crystal Garden has room ID 9003.", "user", "Crystal Garden", "rooms")
        hits = self.b.search("What is the room ID of the crystal place?")
        self.assertEqual(matches(hits), [])
        self.assertTrue(any("ambiguous name" in n for h in hits for n in h.detail["why"]["notes"]))


class ConflictTests(Base):
    def test_both_sides_shown_together_even_beyond_the_limit(self):
        a = self.b.learn("The east fountain is working again.", "user", "east fountain", "objects")
        self.b.link(a, self.ids[3], "contradicts")
        hits = self.b.search(QUESTIONS[5], limit=1)
        ids = [h.record_id for h in hits]
        self.assertIn(str(self.ids[3]), ids)
        self.assertIn(str(a), ids)                              # pulled in despite limit=1
        g = {h.detail.get("conflict_group") for h in hits if h.record_id in (str(a), str(self.ids[3]))}
        self.assertEqual(len(g), 1)
        pos = sorted(ids.index(x) for x in (str(a), str(self.ids[3])))
        self.assertEqual(pos[1] - pos[0], 1)                    # next to each other
        for h in hits:
            self.assertFalse(h.established)
            self.assertIn("Conflicting", h.display())

    def test_low_scoring_side_is_not_hidden(self):
        weak = self.b.learn("Nope.", "user", "", "")              # matches nothing on its own
        self.b.link(weak, self.ids[3], "contradicts")
        ids = [h.record_id for h in self.b.search(QUESTIONS[5])]
        self.assertIn(str(weak), ids)


class TrustTests(Base):
    def _verify(self, cid):
        src = self.b.add_source("screenshot", title="proof", content_hash=f"h{cid}")
        self.b.add_evidence(cid, "direct", src)
        self.b.review(cid, "verified")

    def test_verified_first_when_relevance_is_comparable(self):
        twin = self.b.learn("The Demo Garden room ID is 9001.", "user", "Demo Garden", "rooms")
        self._verify(twin)
        hits = self.b.search(QUESTIONS[1])
        self.assertEqual(hits[0].record_id, str(twin))
        self.assertTrue(hits[0].established)

    def test_relevance_still_matters(self):
        weak = self.b.learn("Gardens are nice.", "user", "gardens", "misc")
        self._verify(weak)
        hits = self.b.search(QUESTIONS[1])
        self.assertEqual(hits[0].record_id, str(self.ids[0]))     # unverified but on point

    def test_outdated_last_and_rejected_excluded(self):
        old = self.b.learn("The Demo Garden had room ID 8000.", "user", "Demo Garden", "rooms")
        self.b.review(old, "outdated")
        gone = self.b.learn("The Demo Garden room ID is 1.", "user", "Demo Garden", "rooms")
        self.b.review(gone, "rejected")
        hits = self.b.search(QUESTIONS[1])
        ids = [h.record_id for h in hits]
        self.assertNotIn(str(gone), ids)
        self.assertEqual(ids[-1], str(old))
        self.assertIn(str(gone), [h.record_id for h in self.b.search(QUESTIONS[1],
                                                                     include_rejected=True)])

    def test_repeated_statements_are_merged_and_gain_nothing(self):
        before = self.top(QUESTIONS[1]).score
        for _ in range(4):
            self.b.learn("The Demo Garden has room ID 9001.", "user", "Demo Garden", "rooms")
        hits = self.b.search(QUESTIONS[1])
        same = [h for h in hits if h.text == "The Demo Garden has room ID 9001."]
        self.assertEqual(len(same), 1)
        self.assertEqual(len(same[0].detail["duplicates"]), 4)
        self.assertLessEqual(same[0].score, before + 1e-9)


class PrivacyTests(Base):
    def test_other_peoples_private_claims_never_influence_results(self):
        before = [(h.record_id, h.score) for h in self.b.search(QUESTIONS[1])]
        ana = self.b.store.add_contributor("Ana")
        for i in range(30):                                      # would change word rarity
            self.b.learn(f"The Demo Garden room ID is {5000 + i}.", "user", "Demo Garden",
                         "rooms", by=ana)
        secret = self.b.learn("Secret Vault Alpha has room ID 7777.", "user", "Secret Vault Alpha",
                              "rooms", by=ana)
        after = [(h.record_id, h.score) for h in self.b.search(QUESTIONS[1])]
        self.assertEqual(before, after)                          # identical results and scores
        mine = self.b.search("What is the room ID of Secret Vault Alpha?")
        self.assertTrue(all(h.record_id != str(secret) and "7777" not in h.text for h in mine))
        self.assertTrue(all(h.detail["why"]["subjects"] == [] for h in mine))   # name unknown to me
        theirs = self.b.search("What is the room ID of Secret Vault Alpha?", viewer=ana)
        self.assertIn("7777", theirs[0].text)

    def test_duplicate_counts_and_conflicts_only_from_visible_claims(self):
        ana = self.b.store.add_contributor("Ana")
        self.b.learn("The Demo Garden has room ID 9001.", "user", "Demo Garden", "rooms", by=ana)
        h = self.top(QUESTIONS[1])
        self.assertNotIn("duplicates", h.detail)                # Ana's copy is invisible to me
        hers = self.b.learn("The east fountain works.", "user", "east fountain", by=ana)
        mine2 = self.b.learn("The east fountain is broken for sure.", "user", "east fountain")
        self.b.link(mine2, self.ids[3], "contradicts")
        ids = [x.record_id for x in self.b.search(QUESTIONS[5])]
        self.assertNotIn(str(hers), ids)

    def test_quiz_and_atlas_only_for_the_local_user(self):
        from puzzle_assistant.qa.memory import Memory
        from puzzle_assistant.qa.model import Answer, Question
        q = tmp("habbosk.db")
        m = Memory(q)
        m.save(Question("PREGUNTA", "¿Quién cuida el Demo Garden?", ["Lina", "Leo"]),
               Answer(choice="Lina", source="game", verified=True))
        m.close()
        b = Brain(tmp(), q, "")
        ana = b.store.add_contributor("Ana")
        self.assertTrue(any(h.module == "quiz_memory" for h in b.search("¿Quién cuida el Demo Garden?")))
        self.assertFalse(any(h.module == "quiz_memory"
                             for h in b.search("¿Quién cuida el Demo Garden?", viewer=ana)))
        b.close()


class EquivalenceTests(Base):
    def test_fts_and_fallback_agree(self):
        if not self.b.store.fts:
            self.skipTest("SQLite without FTS5 here: only the fallback exists")
        extra = list(QUESTIONS.values()) + ["Which fountain is broken?", "¿Qué fuente está apagada?",
                                            "Who is the keeper of the fountain?", "garden", "9001"]
        ana = self.b.store.add_contributor("Ana")
        self.b.learn("Private garden note 9001.", "user", "notes", by=ana)
        for viewer in (None, ana):
            for q in extra:
                a = [(h.module, h.record_id, h.score) for h in self.b.search(q, viewer=viewer,
                                                                             use_fts=True)]
                f = [(h.module, h.record_id, h.score) for h in self.b.search(q, viewer=viewer,
                                                                             use_fts=False)]
                self.assertEqual(a, f, q)


class MigrationAndSpeedTests(unittest.TestCase):
    def test_v2_database_upgrade_keeps_every_row(self):
        path = tmp()
        b = Brain(path, "", "")
        c = b.learn("The Demo Garden has room ID 9001.", "user", "Demo Garden")
        src = b.add_source("screenshot", title="x", content_hash="h")
        b.add_evidence(c, "direct", src)
        b.correct(c, "The Demo Garden has room ID 9010.", reason="typo")
        b.close()
        con = sqlite3.connect(path)                              # make it a v2 file
        con.execute("DROP TABLE claim_index")
        con.execute("DROP TABLE IF EXISTS claim_fts")
        con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        con.execute("DELETE FROM meta WHERE key='index_version'")
        con.commit()
        tables = ["contributors", "sources", "sections", "claims", "evidence", "claim_links",
                  "claim_versions", "claim_events"]
        dump = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
        con.close()
        b = Brain(path, "", "")
        self.assertEqual(b.store.schema_version, 3)
        self.assertEqual(b.search("room ID Demo Garden")[0].record_id, str(c))   # index rebuilt
        b.close()
        con = sqlite3.connect(path)
        for t in tables:
            self.assertEqual(con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall(), dump[t], t)
        con.close()

    def test_speed_with_10000_claims(self):
        b = Brain(tmp(), "", "")
        db = b.store
        words = ["garden", "fountain", "archway", "courtyard", "plaza", "shop", "bank", "casino"]
        with db._lock:
            for i in range(10000):
                b.store.add_claim(f"The {words[i % 8]} number {i} is in area {i % 97}.", "user",
                                  subject=f"{words[i % 8]} {i}")
        b.learn("The Demo Garden has room ID 9001.", "user", "Demo Garden")
        t = time.perf_counter()
        hits = b.search(QUESTIONS[1])
        took = time.perf_counter() - t
        b.close()
        self.assertEqual(hits[0].text, "The Demo Garden has room ID 9001.")
        print(f"\n[B2 speed] 10,001 claims: search took {took * 1000:.0f} ms")
        self.assertLess(took, 2.0)


if __name__ == "__main__":
    unittest.main()
