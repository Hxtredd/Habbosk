"""SK Brain B2 retrieval: the owner's nine regression questions on the
original five Demo Garden claims, negation, compound questions, subjects,
synonyms, similar names, conflicts, trust ranking, duplicates, privacy
(including linked private claims), FTS5 / fallback equivalence, migration
safety and speed."""
import os
import sqlite3
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain import Brain  # noqa: E402

# the owner's B1 experiment, exactly (source SK-BRAIN-TEST-001, unverified, direct evidence)
ORIGINAL = ["The Demo Garden has permanent room ID 900001.",
            "The Demo Garden is reached through the eastern doorway in the Welcome Hall.",
            "The northern archway leads to the Crystal Courtyard.",
            "Mira is responsible for maintaining the garden's fountains and plants.",
            "The central fountain is currently disabled."]
# the subjects / categories the owner used are not known: tested both without
# subjects and with these plausible ones
SUBJECTS = ["Demo Garden", "Demo Garden", "northern archway", "Mira", "central fountain"]

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


def load_original(b, with_subjects):
    src = b.add_source("document", title="SK Brain test material", reference="SK-BRAIN-TEST-001",
                       raw_text="\n".join(ORIGINAL))
    sec = b.add_section(src, "\n".join(ORIGINAL), "SK-BRAIN-TEST-001")
    ids = []
    for i, text in enumerate(ORIGINAL):
        cid = b.learn(text, "imported", SUBJECTS[i] if with_subjects else "", "", sec)
        b.add_evidence(cid, "direct", src, f"line {i + 1}")
        ids.append(cid)
    return ids


class Base(unittest.TestCase):
    SUBJ = True

    def setUp(self):
        self.b = Brain(tmp(), "", "")
        self.ids = load_original(self.b, self.SUBJ)

    def tearDown(self):
        self.b.close()

    def top(self, q, **kw):
        hits = self.b.search(q, **kw)
        return hits[0] if hits else None


class RegressionTests(Base):
    """The owner's nine questions (claims with subjects)."""

    def _top_matches(self, n, i):
        h = self.top(QUESTIONS[n])
        self.assertIsNotNone(h, n)
        self.assertEqual(h.record_id, str(self.ids[i]), (n, h.text))
        self.assertTrue(h.detail["why"]["matches_question"], (n, h.detail["why"]))
        self.assertIn("Unverified", h.display())            # never shown as fact
        return h

    def test_q1_room_id(self):
        self.assertIn("900001", self._top_matches(1, 0).text)

    def test_q2_how_to_get_there(self):
        self._top_matches(2, 1)

    def test_q3_northern_archway(self):
        self._top_matches(3, 2)

    def test_q4_who_maintains(self):
        self._top_matches(4, 3)

    def test_q5_disabled_fountain(self):
        self._top_matches(5, 4)

    def test_q6_q9_no_invented_caretaker(self):
        for n in (6, 9):
            hits = self.b.search(QUESTIONS[n])
            self.assertEqual(matches(hits), [], (n, [h.text for h in matches(hits)]))
            for h in hits:
                # Mira's claim never mentions the Crystal Courtyard
                if h.record_id == str(self.ids[3]):
                    self.assertTrue(any("not mentioned" in x or "different subject" in x
                                        for x in h.detail["why"]["notes"]), h.detail["why"])
                # the archway claim mentions it only as a destination (knowable with subjects)
                if h.record_id == str(self.ids[2]) and self.SUBJ:
                    self.assertTrue(any("but is about" in x for x in h.detail["why"]["notes"]))

    def test_q7_number_is_the_id(self):
        self._top_matches(7, 0)

    def test_q8_unrelated_returns_nothing(self):
        self.assertEqual(self.b.search(QUESTIONS[8]), [])
        self.assertFalse(self.b.ask(QUESTIONS[8]).answered)

    def test_source_and_status_preserved(self):
        for cid in self.ids:
            e = self.b.explain(cid)
            self.assertEqual(e["claim"]["status"], "submitted")
            self.assertEqual(e["evidence"][0]["reference"], "SK-BRAIN-TEST-001")
            self.assertEqual(e["evidence"][0]["kind"], "direct")


class RegressionNoSubjectTests(RegressionTests):
    """Same nine questions when the claims were entered without subjects."""
    SUBJ = False


class NegationTests(Base):
    def test_opposite_statements_never_match(self):
        neg_en = self.b.learn("The central fountain is not disabled.", "user", "central fountain")
        neg_es = self.b.learn("La fuente central no está apagada.", "user", "central fountain")
        for q in ("Which fountain is disabled?", "¿Qué fuente está apagada?"):
            hits = self.b.search(q)
            got = {h.record_id: h for h in hits}
            for cid in (neg_en, neg_es):
                if str(cid) in got:
                    self.assertFalse(got[str(cid)].detail["why"]["matches_question"], q)
                    self.assertTrue(any("opposite statement" in n
                                        for n in got[str(cid)].detail["why"]["notes"]))
            self.assertIn(str(self.ids[4]), [h.record_id for h in matches(hits)], q)
        for q, want in (("Which fountain is not disabled?", neg_en),
                        ("¿Qué fuente no está apagada?", neg_es)):
            hits = self.b.search(q)
            self.assertIn(str(want), [h.record_id for h in matches(hits)], q)
            self.assertNotIn(str(self.ids[4]), [h.record_id for h in matches(hits)], q)

    def test_contractions_and_never(self):
        a = self.b.learn("The central fountain isn't disabled.", "user", "central fountain")
        c = self.b.learn("The central fountain was never disabled.", "user", "central fountain")
        got = {h.record_id: h for h in self.b.search("Which fountain is disabled?")}
        for cid in (a, c):
            self.assertFalse(got[str(cid)].detail["why"]["matches_question"])


class CompoundTests(Base):
    def test_claim_meeting_one_requirement_is_not_a_match(self):
        cases = ["Who maintains the fountains and which fountain is disabled?",
                 "What is the room ID of the Demo Garden and how do I get there?"]
        for q in cases:
            hits = self.b.search(q)
            self.assertEqual(matches(hits), [], (q, [h.text for h in matches(hits)]))
            self.assertTrue(any("answers only part of the question" in n
                                for h in hits for n in h.detail["why"]["notes"]), q)

    def test_claim_meeting_all_requirements_matches(self):
        both = self.b.learn("Mira maintains the central fountain, which is currently disabled.",
                            "user", "central fountain")
        hits = self.b.search("Who maintains the fountain and is it disabled?")
        self.assertEqual([h.record_id for h in matches(hits)], [str(both)])


class SubjectTests(Base):
    def test_mentioned_room_is_not_the_subject(self):
        cmp_ = self.b.learn("The Welcome Hall is larger than the Demo Garden.", "user",
                            "Welcome Hall")
        for q in (QUESTIONS[1], "Which room is the Demo Garden next to?"):
            for h in matches(self.b.search(q)):
                self.assertNotEqual(h.record_id, str(cmp_), q)
        # the doorway claim is about the Demo Garden, not the Welcome Hall
        hits = self.b.search("What is the room ID of the Welcome Hall?")
        self.assertEqual(matches(hits), [])
        self.assertTrue(all(h.record_id != str(self.ids[1]) or not h.detail["why"]["matches_question"]
                            for h in hits))

    def test_route_to_a_destination_counts_for_route_questions(self):
        hits = self.b.search("How do I get to the Crystal Courtyard?")
        self.assertEqual(matches(hits)[0].record_id, str(self.ids[2]))   # "leads to the ..."

    def test_different_subject_never_matches(self):
        hits = self.b.search(QUESTIONS[9])
        for h in hits:
            if h.record_id == str(self.ids[3]):
                self.assertFalse(h.detail["why"]["matches_question"])


class SynonymTests(Base):
    def test_synonyms_and_word_forms(self):
        cases = {"Who is the caretaker of the fountains?": 3,      # caretaker ~ maintaining
                 "Who is the keeper of the fountain?": 3,
                 "What is the identifier of the Demo Garden?": 0,
                 "Which fountain is broken?": 4,                   # broken ~ disabled
                 "Where does the north arch go?": 2,               # north/arch/go
                 "¿Qué fuente está apagada?": 4,                   # Spanish
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
        annex = self.b.learn("The Demo Garden Annex has room ID 900002.", "user",
                             "Demo Garden Annex")
        h = self.top(QUESTIONS[1])
        self.assertEqual(h.record_id, str(self.ids[0]))
        other = [x for x in self.b.search(QUESTIONS[1]) if x.record_id == str(annex)]
        self.assertTrue(not other or not other[0].detail["why"]["matches_question"])
        h2 = self.top("What is the room ID of the Demo Garden Annex?")
        self.assertEqual(h2.record_id, str(annex))
        self.assertTrue(h2.detail["why"]["matches_question"])

    def test_partial_name_is_ambiguous_not_resolved(self):
        self.b.learn("The Crystal Garden has room ID 900003.", "user", "Crystal Garden")
        self.b.learn("The Crystal Courtyard has a glass roof.", "user", "Crystal Courtyard")
        hits = self.b.search("What is the room ID of the crystal place?")
        self.assertEqual(matches(hits), [])
        self.assertTrue(any("ambiguous name" in n for h in hits for n in h.detail["why"]["notes"]))


class ConflictTests(Base):
    def test_both_sides_shown_together_even_beyond_the_limit(self):
        a = self.b.learn("The central fountain is working again.", "user", "central fountain")
        self.b.link(a, self.ids[4], "contradicts")
        hits = self.b.search(QUESTIONS[5], limit=1)
        ids = [h.record_id for h in hits]
        self.assertIn(str(self.ids[4]), ids)
        self.assertIn(str(a), ids)                              # pulled in despite limit=1
        g = {h.detail.get("conflict_group") for h in hits if h.record_id in (str(a), str(self.ids[4]))}
        self.assertEqual(len(g), 1)
        pos = sorted(ids.index(x) for x in (str(a), str(self.ids[4])))
        self.assertEqual(pos[1] - pos[0], 1)                    # next to each other
        for h in hits:
            self.assertFalse(h.established)
            self.assertIn("Conflicting", h.display())

    def test_low_scoring_side_is_not_hidden(self):
        weak = self.b.learn("Nope.", "user", "", "")              # matches nothing on its own
        self.b.link(weak, self.ids[4], "contradicts")
        ids = [h.record_id for h in self.b.search(QUESTIONS[5])]
        self.assertIn(str(weak), ids)


class TrustTests(Base):
    def _verify(self, cid):
        src = self.b.add_source("screenshot", title="proof", content_hash=f"h{cid}")
        self.b.add_evidence(cid, "direct", src)
        self.b.review(cid, "verified")

    def test_verified_first_when_relevance_is_comparable(self):
        twin = self.b.learn("The Demo Garden room ID is permanently 900001.", "user", "Demo Garden")
        self._verify(twin)
        hits = self.b.search(QUESTIONS[1])
        self.assertEqual(hits[0].record_id, str(twin))
        self.assertTrue(hits[0].established)

    def test_relevance_still_matters(self):
        weak = self.b.learn("Gardens are nice.", "user", "gardens")
        self._verify(weak)
        self.assertEqual(self.b.search(QUESTIONS[1])[0].record_id, str(self.ids[0]))

    def test_outdated_last_and_rejected_excluded(self):
        old = self.b.learn("The Demo Garden had room ID 8000.", "user", "Demo Garden")
        self.b.review(old, "outdated")
        gone = self.b.learn("The Demo Garden room ID is 1.", "user", "Demo Garden")
        self.b.review(gone, "rejected")
        ids = [h.record_id for h in self.b.search(QUESTIONS[1])]
        self.assertNotIn(str(gone), ids)
        self.assertEqual(ids[-1], str(old))
        self.assertIn(str(gone), [h.record_id for h in self.b.search(QUESTIONS[1],
                                                                     include_rejected=True)])

    def test_repeated_statements_are_merged_and_gain_nothing(self):
        before = self.top(QUESTIONS[1]).score
        for _ in range(4):
            self.b.learn(ORIGINAL[0], "user", SUBJECTS[0])
        same = [h for h in self.b.search(QUESTIONS[1]) if h.text == ORIGINAL[0]]
        self.assertEqual(len(same), 1)
        self.assertEqual(len(same[0].detail["duplicates"]), 4)
        self.assertLessEqual(same[0].score, before + 1e-9)


class PrivacyTests(Base):
    def test_other_peoples_private_claims_never_influence_results(self):
        before = [(h.record_id, h.score) for h in self.b.search(QUESTIONS[1])]
        ana = self.b.store.add_contributor("Ana")
        for i in range(30):                                      # would change word rarity
            self.b.learn(f"The Demo Garden room ID is {5000 + i}.", "user", "Demo Garden", by=ana)
        secret = self.b.learn("Secret Vault Alpha has room ID 7777.", "user", "Secret Vault Alpha",
                              by=ana)
        after = [(h.record_id, h.score) for h in self.b.search(QUESTIONS[1])]
        self.assertEqual(before, after)                          # identical results and scores
        mine = self.b.search("What is the room ID of Secret Vault Alpha?")
        self.assertTrue(all(h.record_id != str(secret) and "7777" not in h.text for h in mine))
        theirs = self.b.search("What is the room ID of Secret Vault Alpha?", viewer=ana)
        self.assertIn("7777", theirs[0].text)

    def test_duplicate_counts_and_conflicts_only_from_visible_claims(self):
        ana = self.b.store.add_contributor("Ana")
        self.b.learn(ORIGINAL[0], "user", SUBJECTS[0], by=ana)
        self.assertNotIn("duplicates", self.top(QUESTIONS[1]).detail)   # her copy is invisible
        hers = self.b.learn("The central fountain works.", "user", "central fountain", by=ana)
        mine2 = self.b.learn("The central fountain is broken for sure.", "user", "central fountain")
        self.b.link(mine2, self.ids[4], "contradicts")
        self.assertNotIn(str(hers), [x.record_id for x in self.b.search(QUESTIONS[5])])

    def test_linked_private_claim_stays_invisible_after_sharing(self):
        st = self.b.store
        ana = st.add_contributor("Ana")
        bob = st.add_contributor("Bob")
        mod = st.add_contributor("Mod", kind="moderator")
        pub = self.b.learn("The Welcome Hall has a red carpet.", "user", "Welcome Hall", by=ana)
        priv = self.b.learn("My locker code in the Welcome Hall is 4411.", "user", "Welcome Hall",
                            by=ana)
        self.b.link(pub, priv, "contradicts", by=ana)            # both personal: both flagged
        self.b.review(priv, "rejected", reviewer=ana)            # resolve on her side
        src = self.b.add_source("screenshot", title="carpet", content_hash="carpet", by=ana)
        self.b.add_evidence(pub, "direct", src, by=ana)
        self.b.review(pub, "verified", reviewer=ana)
        self.b.propose_share(pub, by=ana)
        self.b.approve_share(pub, mod)
        e = self.b.explain(pub, viewer=bob)
        self.assertEqual(e["claim"]["text"], "The Welcome Hall has a red carpet.")
        self.assertEqual(e["links"], [])                         # the link is not shown
        blob = repr(e)
        self.assertNotIn("4411", blob)
        self.assertNotIn("locker", blob)
        self.assertNotIn(f"claim {priv}", blob)                  # not even its id
        self.assertFalse(any(ev.get("related_claim") == priv for ev in e["events"]))
        self.assertIsNone(self.b.claim(priv, viewer=bob))
        hits = self.b.search("Welcome Hall carpet", viewer=bob)
        self.assertEqual([h.record_id for h in hits if h.module == "library"], [str(pub)])
        self.assertEqual(self.b.explain(pub, viewer=ana)["links"][0]["to_claim"], priv)  # owner sees it

    def test_private_link_does_not_flag_a_shared_claim(self):
        st = self.b.store
        ana = st.add_contributor("Ana")
        bob = st.add_contributor("Bob")
        mod = st.add_contributor("Mod", kind="moderator")
        pub = self.b.learn("The Welcome Hall is open all day.", "user", "Welcome Hall", by=ana)
        src = self.b.add_source("screenshot", title="hall", content_hash="hall", by=ana)
        self.b.add_evidence(pub, "direct", src, by=ana)
        self.b.review(pub, "verified", reviewer=ana)
        self.b.propose_share(pub, by=ana)
        self.b.approve_share(pub, mod)
        priv = self.b.learn("The Welcome Hall closes at night.", "user", "Welcome Hall", by=ana)
        self.b.link(priv, pub, "contradicts", by=ana)
        self.assertEqual(self.b.claim(pub, viewer=bob)["status"], "verified")   # nothing leaks
        self.assertEqual(self.b.claim(priv, viewer=ana)["status"], "conflicting")
        e = self.b.explain(pub, viewer=bob)
        self.assertEqual(e["links"], [])
        self.assertNotIn(f"claim {priv}", repr(e))

    def test_stats_never_reveal_other_peoples_sources(self):
        ana = self.b.store.add_contributor("Ana")
        for i in range(3):
            self.b.add_source("screenshot", title=f"ana {i}", by=ana)
        self.assertEqual(self.b.stats(viewer=ana)["sources"], 3)
        self.assertEqual(self.b.stats()["sources"], 1)          # only my SK-BRAIN-TEST-001
        bob = self.b.store.add_contributor("Bob")
        self.assertEqual(self.b.stats(viewer=bob)["sources"], 0)
        self.assertEqual(self.b.stats(viewer=bob)["claims"], 0)

    def test_quiz_and_atlas_only_for_the_local_user(self):
        from puzzle_assistant.qa.memory import Memory
        from puzzle_assistant.qa.model import Answer, Question
        q = tmp("habbosk.db")
        m = Memory(q)
        m.save(Question("PREGUNTA", "¿Quién cuida el Demo Garden?", ["Mira", "Leo"]),
               Answer(choice="Mira", source="game", verified=True))
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
        extra = list(QUESTIONS.values()) + [
            "Which fountain is broken?", "¿Qué fuente está apagada?", "Which fountain is not disabled?",
            "Who is the keeper of the fountain?", "garden", "900001",
            "Who maintains the fountains and which fountain is disabled?"]
        ana = self.b.store.add_contributor("Ana")
        self.b.learn("Private garden note 900001.", "user", "notes", by=ana)
        self.b.learn("The central fountain is not disabled.", "user", "central fountain")
        for viewer in (None, ana):
            for q in extra:
                a = [(h.module, h.record_id, h.score, h.detail["why"]["matches_question"])
                     for h in self.b.search(q, viewer=viewer, use_fts=True)]
                f = [(h.module, h.record_id, h.score, h.detail["why"]["matches_question"])
                     for h in self.b.search(q, viewer=viewer, use_fts=False)]
                self.assertEqual(a, f, q)


class MigrationAndSpeedTests(unittest.TestCase):
    TABLES = ["contributors", "sources", "sections", "claims", "evidence", "claim_links",
              "claim_versions"]

    def test_v2_database_upgrade_keeps_every_row(self):
        path = tmp()
        b = Brain(path, "", "")
        ids = load_original(b, True)
        b.correct(ids[0], "The Demo Garden has permanent room ID 900001 (checked).", reason="note")
        other = b.learn("The central fountain is working again.", "user", "central fountain")
        b.link(other, ids[4], "contradicts")
        b.close()
        con = sqlite3.connect(path)                              # turn it into a real v2 file
        con.execute("DROP TABLE claim_index")
        con.execute("DROP TABLE IF EXISTS claim_fts")
        con.execute("ALTER TABLE claim_events DROP COLUMN related_claim")
        con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        con.execute("DELETE FROM meta WHERE key='index_version'")
        con.commit()
        self.assertNotIn("related_claim", [r[1] for r in con.execute("PRAGMA table_info(claim_events)")])
        dump = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in self.TABLES}
        events = con.execute("SELECT id, claim_id, event, old_status, new_status, by, at, note "
                             "FROM claim_events ORDER BY id").fetchall()
        con.close()
        b = Brain(path, "", "")
        self.assertEqual(b.store.schema_version, 3)
        self.assertEqual(b.search(QUESTIONS[1])[0].record_id, str(ids[0]))     # index rebuilt
        b.close()
        con = sqlite3.connect(path)
        for t in self.TABLES:
            self.assertEqual(con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall(), dump[t], t)
        self.assertEqual(con.execute("SELECT id, claim_id, event, old_status, new_status, by, at, "
                                     "note FROM claim_events ORDER BY id").fetchall(), events)
        con.close()

    def test_speed_with_10000_claims(self):
        """Measured, not just bounded. The 50 ms goal is reported against."""
        b = Brain(tmp(), "", "")
        words = ["garden", "fountain", "archway", "courtyard", "plaza", "shop", "bank", "casino"]
        with b.store._lock:
            for i in range(10000):
                b.store.add_claim(f"The {words[i % 8]} number {i} is in area {i % 97}.", "user",
                                  subject=f"{words[i % 8]} {i}")
        load_original(b, True)
        b.search("x")
        report = []
        for label, q in (("typical", QUESTIONS[3]), ("no match", QUESTIONS[8]),
                         ("worst case: common words in every claim", QUESTIONS[1])):
            for fts in (True, False):
                t = time.perf_counter()
                for _ in range(3):
                    b.search(q, use_fts=fts)
                ms = (time.perf_counter() - t) / 3 * 1000
                report.append((label, "FTS5" if fts else "fallback", ms))
        self.assertEqual(b.search(QUESTIONS[1])[0].text, ORIGINAL[0])
        b.close()
        print("\n[B2 speed, 10,005 claims; goal 50 ms]")
        for label, path_, ms in report:
            print(f"   {label:42s} {path_:8s} {ms:6.0f} ms {'(within goal)' if ms <= 50 else '(over goal)'}")
        self.assertLess(max(ms for _, _, ms in report), 1000)   # regression guard, not the goal


if __name__ == "__main__":
    unittest.main()
