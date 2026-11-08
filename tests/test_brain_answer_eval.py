"""B4 hard gates: zero false Established, zero false Documented, zero false
citation support; plus floors so later stages cannot quietly get worse."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import brain_answer_eval as A  # noqa: E402
import brain_citation_set as C  # noqa: E402
import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402
from puzzle_assistant.brain.respond import quote_supports  # noqa: E402


class PuertoNubeGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = A.evaluate()["rows"]

    def test_every_question_in_its_expected_category(self):
        wrong = [(r["q"], r["asker"], r["expect"], r["got"]) for r in self.rows if not r["ok"]]
        self.assertEqual(wrong, [])

    def test_zero_false_established_and_documented(self):
        for cat in ("established", "documented"):
            false = [r["q"] for r in self.rows if r["got"][0] == cat and r["expect"][0] != cat]
            self.assertEqual(false, [], cat)

    def test_everyday_personal_questions_are_unknown(self):
        general = {q for q, *_ in A.GENERAL}
        self.assertTrue(general)
        self.assertEqual({r["got"][0] for r in self.rows if r["q"] in general}, {"unknown"})


class CitationGates(unittest.TestCase):
    def test_zero_false_support(self):
        false = [(t, q, l) for t, s, q, l in C.ITEMS if l != "supports" and quote_supports(t, s, q)]
        self.assertEqual(false, [])

    def test_the_set_covers_every_label_and_genuine_support_is_accepted(self):
        self.assertEqual({l for *_, l in C.ITEMS}, {"supports", "contradicts", "partial", "unrelated"})
        ok = sum(1 for t, s, q, l in C.ITEMS if l == "supports" and quote_supports(t, s, q))
        self.assertGreaterEqual(ok, 10)                     # floor (12 labelled "supports")


class RealisticSets(unittest.TestCase):
    """Villa Brisa / Ciudad Coral, scored by their kinds."""

    def sweep(self, mod, questions):
        d = tempfile.mkdtemp()
        b, ids, users = mod.build(os.path.join(d, "s.db"))
        key_of = {v: k for k, v in ids.items()}
        counts = {"established": 0, "documented": 0, "unknown": 0}
        try:
            for q, kind, expect, asker in questions:
                r = b.respond(q, viewer=users[asker])
                counts[r.category] += 1
                used = [key_of[c.claim_id] for c in r.citations]
                with self.subTest(q=q, asker=asker):
                    self.assertNotEqual(r.category, "ai_generated")
                    if kind in ("unknown", "conflict", "ambiguous", "privacy"):
                        self.assertEqual(r.category, "unknown")
                    # these datasets have no source quotes: nothing can be Documented
                    self.assertNotEqual(r.category, "documented")
                    if r.category == "established":
                        self.assertIn(kind, ("answer", "multi"))     # multi = a chained answer
                        self.assertTrue(set(used) & set(expect), used)
                        self.assertTrue(all(c.status == "verified" for c in r.citations))
                        self.assertTrue(all(mod.CLAIMS[k][3] == asker or mod.CLAIMS[k][2] == "shared"
                                            for k in used))
                    if r.category == "unknown":
                        self.assertIsNone(r.value)
                        self.assertTrue(r.reason)
        finally:
            b.close()
            shutil.rmtree(d, ignore_errors=True)
        return counts

    def test_villa_brisa(self):
        c = self.sweep(E1, E1.QUESTIONS + E1.HELDOUT)
        self.assertGreaterEqual(c["established"], 10)      # floor (B4: 10)

    def test_ciudad_coral(self):
        c = self.sweep(E2, E2.QUESTIONS)
        self.assertGreaterEqual(c["established"], 10)      # floor (B4: 10)


if __name__ == "__main__":
    unittest.main()
