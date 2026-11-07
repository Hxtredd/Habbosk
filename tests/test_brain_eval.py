"""B2 validation: retrieval regression tests and the realistic evaluation's
guarantees (dataset and scoring in tests/brain_eval.py)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import brain_eval as E  # noqa: E402
import brain_eval_city as C  # noqa: E402
from puzzle_assistant.brain.query import analyze  # noqa: E402
from puzzle_assistant.brain.text import stem, terms  # noqa: E402


class EvalSetup:
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.b, cls.ids, cls.users = E.build(os.path.join(cls.dir, "eval.db"))
        cls.key = {str(v): k for k, v in cls.ids.items()}

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def answers(self, q):
        return [self.key[h.record_id] for h in self.b.search(q)
                if h.detail["why"]["matches_question"]]

    def first(self, q):
        return self.key[self.b.search(q)[0].record_id]


class RetrievalFixTests(EvalSetup, unittest.TestCase):
    def test_a_different_direction_is_not_the_answer(self):
        self.assertEqual(self.answers("Where does the south door of the Plaza Mayor lead?"), [])
        self.assertEqual(self.answers("Where does the north door of the Plaza Mayor lead?"),
                         ["plaza_north"])

    def test_matching_only_the_name_is_not_an_answer(self):
        self.assertEqual(self.answers("What connects to the Biblioteca Antigua?"), ["luna_arch"])
        self.assertNotIn("fountain_ok", self.answers("Who is the caretaker of the Plaza Mayor?"))

    def test_connect_and_goes_to_are_route_questions(self):
        self.assertIn("plaza_east",
                      self.answers("Which exit of the Plaza Mayor goes to the Sala Azul?"))

    def test_a_no_answers_a_yes_no_question(self):
        self.assertEqual(self.answers("Does the taxi work in the Cementerio Viejo?")[:1],
                         ["taxi_cement"])
        self.assertIn("pets_biblio", self.answers("Are pets allowed in the Biblioteca Antigua?"))
        # ...but not a "which / what" question asking for the opposite
        self.assertNotIn("pets_biblio", self.answers("Which rooms allow pets?"))

    def test_spanish_question_frame_words_are_not_content(self):
        self.assertEqual(terms("¿Cómo llego al Cementerio Viejo?"), ["cementerio", "viejo"])
        self.assertIn("bosque_west", self.answers("¿Cómo llego al Cementerio Viejo?"))

    def test_word_forms_meet_at_one_stem(self):
        for a, b in (("close", "closes"), ("closed", "close"), ("rule", "rules"),
                     ("code", "codes"), ("gate", "gates"), ("place", "places"),
                     ("calle", "calles"), ("fuente", "fuentes"), ("door", "doors")):
            self.assertEqual(stem(a), stem(b), (a, b))
        self.assertEqual(self.answers("When does the Biblioteca Antigua close?"),
                         ["biblio_close"])
        self.assertEqual(terms("make sure"), ["mak"])           # 'sure' is not 'sur' (south)


class EvaluationGuarantees(unittest.TestCase):
    """Hard guarantees hold on every question; accuracy floors are the
    measured B2 values (a drop is a regression, not a target)."""

    @classmethod
    def setUpClass(cls):
        cls.main = E.evaluate(questions=E.QUESTIONS)
        cls.held = E.evaluate(questions=E.HELDOUT)

    def test_privacy_unknowns_conflicts_never_fail(self):
        for res in (self.main, self.held):
            for kind in ("privacy", "unknown", "conflict", "ambiguous"):
                ok, _, n = res["summary"][kind]
                bad = [r["q"] for r in res["rows"] if r["kind"] == kind and not r["ok"]]
                self.assertEqual(ok, n, f"{kind}: {bad}")

    def test_accuracy_does_not_drop(self):
        self.assertGreaterEqual(self.main["summary"]["answer"][0], 21)
        self.assertGreaterEqual(self.main["summary"]["answer"][1], 23)
        self.assertGreaterEqual(self.held["summary"]["answer"][0], 13)
        self.assertGreaterEqual(self.held["summary"]["answer"][1], 14)

    def test_fts5_and_fallback_identical_on_the_dataset(self):
        for qs, res in ((E.QUESTIONS, self.main), (E.HELDOUT, self.held)):
            fb = E.evaluate(use_fts=False, questions=qs)
            self.assertEqual([r["got"] for r in res["rows"]], [r["got"] for r in fb["rows"]])
            self.assertEqual([r["match"] for r in res["rows"]], [r["match"] for r in fb["rows"]])


class CityFixTests(unittest.TestCase):
    """Question reading fixes found with set 2 (Ciudad Coral)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.b, cls.ids, cls.users = C.build(os.path.join(cls.dir, "city.db"))
        cls.key = {str(v): k for k, v in cls.ids.items()}

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def hits(self, q):
        return [(self.key[h.record_id], h.detail["why"]["matches_question"])
                for h in self.b.search(q) if h.module == "library"]

    def test_first_word_of_a_question_is_not_a_name(self):
        self.assertEqual([e.name for e in analyze("Cuántas monedas da la misión?", []).entities], [])
        self.assertEqual([e.name for e in analyze("Room ID of the Sala Azul", []).entities],
                         ["Sala Azul"])
        self.assertEqual(self.hits("¿Cuántas monedas da la misión del Faro del Norte?")[0],
                         ("quest_norte", True))

    def test_lowercase_topic_is_not_a_required_subject(self):
        self.assertEqual(self.hits("Is trading allowed in the Mercado Nocturno after midnight?")[0],
                         ("trade_noct", True))

    def test_part_of_several_names_is_ambiguous(self):
        h = self.hits("What is the room ID of the Faro?")
        self.assertEqual({k for k, _ in h[:2]}, {"faro_norte_id", "faro_sur_id"})
        self.assertFalse(any(m for _, m in h))               # never resolved by a guess
        self.assertEqual(self.hits("Faro del Sur room ID")[0], ("faro_sur_id", True))


class CityEvaluationGuarantees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = C.evaluate()

    def test_hard_guarantees(self):
        for kind in ("privacy", "unknown", "conflict", "ambiguous", "compound"):
            ok, n = self.res["summary"][kind]
            self.assertEqual(ok, n, [r["q"] for r in self.res["rows"]
                                     if r["kind"] == kind and not r["ok"]])
        self.assertEqual(self.res["status_errors"], [])
        self.assertEqual(self.res["sweep_errors"], [])
        multi = [r for r in self.res["rows"] if r["kind"] == "multi"]
        self.assertFalse(any(r["match"] for r in multi))     # no unsupported conclusion

    def test_accuracy_does_not_drop(self):
        self.assertGreaterEqual(self.res["summary"]["answer"][0], 22)

    def test_fts5_and_fallback_identical(self):
        fb = C.evaluate(use_fts=False)
        self.assertEqual([(r["got"], r["match"]) for r in self.res["rows"]],
                         [(r["got"], r["match"]) for r in fb["rows"]])


if __name__ == "__main__":
    unittest.main()
