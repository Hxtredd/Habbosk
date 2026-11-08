"""B4 respond(): categories, citations, wording, clarification, related
records (never answers), quiz memory / World Atlas as related records
only, and privacy (marker texts never leak between users or into shared
answers). Synthetic data in temporary folders only."""
import dataclasses
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import brain_answer_eval as A  # noqa: E402
from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain.respond import language  # noqa: E402
from puzzle_assistant.brain.store import BrainError  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import Answer, Question  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402


class NubeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="sk_b4_respond_")
        cls.b, cls.ids, cls.users = A.build(os.path.join(cls.dir, "n.db"))

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def ask(self, q, who="ana"):
        return self.b.respond(q, viewer=self.users[who])


class Categories(NubeBase):
    def test_established_cites_only_verified_claims(self):
        r = self.ask("What is the room ID of the Plaza Nube?")
        self.assertEqual((r.category, r.value, r.sentence), ("established", "500100", "500100."))
        self.assertEqual([c.status for c in r.citations], ["verified"])
        self.assertEqual(r.citations[0].evidence[0]["source"], A.GUIDE)

    def test_documented_names_its_source_and_marks_supporting_quotes(self):
        r = self.ask("Who maintains the room the north gate of the Plaza Nube leads to?")
        self.assertEqual((r.category, r.value), ("documented", "Mira"))
        self.assertEqual(r.sentence, f"According to {A.GUIDE}, 2026-10-01 (not reviewed): Mira.")
        by_status = {c.status: c for c in r.citations}
        self.assertTrue(by_status["submitted"].supports)               # the unverified step
        self.assertEqual(by_status["submitted"].evidence[0]["quote"], "Mira maintains the Jardín Nube.")
        self.assertIn("verified", by_status)                            # the verified step

    def test_documented_absence(self):
        r = self.ask("Who is the caretaker of the Faro Nuevo?")
        self.assertEqual((r.category, r.value), ("documented", "none"))
        self.assertIn("there is none", r.sentence)

    def test_unsupported_shows_the_possibility_labelled(self):
        r = self.ask("Who maintains the Muelle Nube?")
        self.assertEqual((r.category, r.reason, r.value, r.possible), ("unknown", "unsupported", None,
                                                                         "Bruno"))
        self.assertIn("no source confirms it", r.sentence)
        self.assertFalse(any(c.supports for c in r.citations))

    def test_conflict_shows_both_sides(self):
        r = self.ask("Who maintains the Granja Nube?")
        self.assertEqual((r.category, r.reason), ("unknown", "sources_disagree"))
        self.assertEqual(sorted(s["value"] for s in r.sides), ["Leo", "Ona"])
        self.assertEqual(len(r.citations), 2)

    def test_ambiguous_asks_which_one_with_b3s_candidates(self):
        r = self.ask("What is the room ID of the Molino?")
        a = self.b.ask("What is the room ID of the Molino?", viewer=self.users["ana"])
        self.assertEqual(r.reason, "ambiguous")
        self.assertEqual(r.clarification["options"], a.steps[-1]["candidates"])
        self.assertEqual(r.clarification["options"], ["Molino Alto", "Molino Bajo"])

    def test_only_outdated_never_answers(self):
        r = self.ask("What is the room ID of the Puente Nube?")
        self.assertEqual((r.category, r.reason, r.value, r.possible),
                         ("unknown", "only_outdated", None, "500400"))
        self.assertEqual([c.status for c in r.citations], ["outdated"])

    def test_missing_never_says_none(self):
        r = self.ask("Who maintains the Torre Nube?")                   # only a rejected claim
        self.assertEqual((r.category, r.reason, r.value, r.possible),
                         ("unknown", "nothing_documented", None, None))
        self.assertIn("does not mean there is none", r.sentence)

    def test_not_understood_lists_related_records_separately(self):
        r = self.ask("Tell me about the Plaza Nube")
        self.assertEqual((r.category, r.reason, r.value), ("unknown", "not_understood", None))
        self.assertTrue(r.related)
        self.assertTrue(all(set(x) == {"text", "label", "status", "module"} for x in r.related))
        self.assertEqual(r.citations, [])

    def test_b3_status_is_kept_for_comparison(self):
        r = self.ask("Who maintains the Granja Nube?")
        self.assertEqual(r.b3_status, "conflicting")


class Language(NubeBase):
    def test_detection_is_conservative(self):
        for q, lang in (("¿Quién mantiene el Jardín Nube?", "es"), ("Quien cuida la Plaza", "es"),
                        ("cuantas monedas da la mision", "es"), ("¡Hola!", "es"),
                        ("Who maintains the Plaza del Puerto?", "en"),
                        ("What is the room ID of the Jardín de las Rosas?", "en"),
                        ("room number of the Sala Azul", "en"), ("", "en")):
            self.assertEqual(language(q), lang, q)

    def test_spanish_wording_and_values(self):
        r = self.ask("¿Cuántas monedas da la misión del Mercado Nube?")
        self.assertEqual((r.language, r.value), ("es", "40 monedas"))
        self.assertTrue(r.sentence.startswith("Según "))
        r = self.ask("¿Quién cuida la Granja Nube?")
        self.assertTrue(r.sentence.startswith("No lo sé"))
        r = self.ask("¿Quién es el encargado del Faro Viejo?")
        self.assertEqual((r.category, r.value, r.sentence),
                         ("established", "ninguno", "No hay ninguno - está documentado."))


class QuizAndAtlasAreRelatedOnly(unittest.TestCase):
    def test_never_an_answer_source(self):
        d = tempfile.mkdtemp(prefix="sk_b4_adapt_")
        try:
            qpath, apath = os.path.join(d, "habbosk.db"), os.path.join(d, "atlas.db")
            m = Memory(qpath)
            m.save(Question("¿QUIÉN SOY?", "Soy un asistente NPC", ["Rubén", "Mateo", "Leo"]),
                   Answer(choice="Leo", explanation="Leo cubre el turno del gimnasio.",
                          source="game", verified=True))
            m.close()
            a = Atlas(apath)
            a.import_input()
            a.close()
            before = [hashlib.sha256(open(p, "rb").read()).hexdigest() for p in (qpath, apath)]
            b = Brain(os.path.join(d, "k.db"), qpath, apath)
            for q in ("asistente NPC Leo gimnasio", "What is the room ID of the Gran Casino?",
                      "Gran Casino"):
                r = b.respond(q)
                self.assertEqual(r.category, "unknown", q)
                self.assertIsNone(r.value)
                self.assertEqual(r.citations, [])
            modules = {x["module"] for x in b.respond("asistente NPC Leo gimnasio").related}
            self.assertIn("quiz_memory", modules)
            b.close()
            self.assertEqual([hashlib.sha256(open(p, "rb").read()).hexdigest()
                              for p in (qpath, apath)], before)
        finally:
            shutil.rmtree(d, ignore_errors=True)


ANA_MARK, LUIS_MARK = "QQANAMARK", "QQLUISMARK"


class PrivacyMarkers(unittest.TestCase):
    """Unique marker texts in each person's private knowledge must never
    reach anyone else's answer, or an answer marked as shared."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="sk_b4_priv_")
        b, ids, users = A.build(os.path.join(cls.dir, "p.db"))
        cls.b, cls.users = b, users
        ana, luis, mod = users["ana"], users["luis"], users["mod"]
        a_src = b.add_source("document", title=f"{ANA_MARK} diary", reference=f"file://{ANA_MARK}",
                             private=True)
        l_src = b.add_source("document", title=f"{LUIS_MARK} notes", reference=f"file://{LUIS_MARK}",
                             private=True, by=luis)
        mine = {}
        for who, mark, src in ((ana, ANA_MARK, a_src), (luis, LUIS_MARK, l_src)):
            for text in (f"Mira maintains the {mark} Garden.", f"The {mark} Hall has room ID 7123.",
                         f"{mark.title()} runs the {mark} Hall."):
                c = b.learn(text, "user", f"{mark} subject", by=who)
                b.add_evidence(c, "direct", src, f"quote {mark}", by=who)
                mine.setdefault(who, c)
        cls.ana_private = mine[ana]
        # a shared claim with (illegitimately) a private source attached by a raw edit
        shared = ids["zoe_plaza"]
        con = sqlite3.connect(os.path.join(cls.dir, "p.db"))
        con.execute("INSERT INTO evidence (claim_id, source_id, kind, locator, added_by, added_at) "
                    "VALUES (?,?,?,?,?,?)", (shared, l_src, "direct", f"leak {LUIS_MARK}", luis, "x"))
        con.commit()
        con.close()
        cls.questions = [q for q, *_ in A.QUESTIONS + A.GENERAL] + [
            f"Who maintains the {ANA_MARK} Garden?", f"Who maintains the {LUIS_MARK} Garden?",
            f"Who runs the {ANA_MARK} Hall?", f"Who runs the {LUIS_MARK} Hall?",
            f"What is the room ID of the {LUIS_MARK} Hall?", "Who runs the Plaza Nube?",
            f"{ANA_MARK} {LUIS_MARK}", "What is the room ID of the Plaza Nube?"]

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def answers(self, viewer):
        for q in self.questions:
            r = self.b.respond(q, viewer=viewer)
            d = dataclasses.asdict(r)
            d.pop("question")                      # the question itself may name anything
            yield q, r, json.dumps(d, ensure_ascii=False)

    def test_markers_only_reach_their_owner(self):
        allowed = {"ana": {ANA_MARK}, "luis": {LUIS_MARK}, "mod": set(), "": {ANA_MARK},
                   "nobody-at-all": set()}
        for who, ok in allowed.items():
            viewer = self.users.get(who, who)
            for q, r, blob in self.answers(viewer):
                for mark in {ANA_MARK, LUIS_MARK} - ok:
                    self.assertNotIn(mark, blob, (who, q))

    def test_answers_using_private_knowledge_are_never_marked_shared(self):
        for who in ("ana", "luis"):
            for q, r, blob in self.answers(self.users[who]):
                if r.audience == "shared":
                    # the audience covers the answer itself: value, sentence, citations,
                    # steps. Related records are always the asker's own view.
                    self.assertTrue(all(c.scope == "shared" for c in r.citations), q)
                    answer = json.dumps([r.value, r.sentence, r.possible,
                                         [dataclasses.asdict(c) for c in r.citations], r.steps],
                                        ensure_ascii=False)
                    self.assertNotIn(ANA_MARK, answer)
                    self.assertNotIn(LUIS_MARK, answer)

    def test_someone_elses_private_source_is_never_cited(self):
        r = self.b.respond("Who runs the Plaza Nube?", viewer=self.users["ana"])
        self.assertEqual(r.category, "established")
        self.assertNotIn(LUIS_MARK, json.dumps(dataclasses.asdict(r), ensure_ascii=False))

    def test_citation_lookup_refuses_claims_the_asker_may_not_use(self):
        st, luis = self.b.store, self.users["luis"]
        public = self.b.add_source("document", title="a public page", by=luis)   # not private
        luis_claim = self.b.learn(f"Luis's own note {LUIS_MARK}.", "user", by=luis)
        self.b.add_evidence(luis_claim, "direct", public, "quote", by=luis)
        self.assertTrue(st.cited_evidence(luis_claim, luis))
        for who in ("ana", "mod"):
            self.assertEqual(st.cited_evidence(luis_claim, self.users[who]), [])
        self.assertEqual(st.cited_evidence(luis_claim, "nobody-at-all"), [])
        self.assertEqual(st.cited_evidence(999999, self.users["ana"]), [])

    def test_private_evidence_cannot_enter_shared_knowledge(self):
        with self.assertRaises(BrainError):
            self.b.propose_share(self.ana_private)
            self.b.review(self.ana_private, "verified", reviewer=self.users["mod"])
            self.b.approve_share(self.ana_private, self.users["mod"])
        self.assertNotEqual(self.b.claim(self.ana_private)["scope"], "shared")


if __name__ == "__main__":
    unittest.main()
