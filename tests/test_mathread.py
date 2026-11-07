"""v2.0 fix: sums on option buttons read by shape (÷ was read as '+' or a
digit, a 0 next to '×' was lost) and never solved from a doubtful reading."""
import os
import sys
import tempfile
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.qa.mathread import looks_like_math  # noqa: E402
from puzzle_assistant.qa.memory import Memory, suspect_sum  # noqa: E402
from puzzle_assistant.qa.model import ORDER, Answer, Question  # noqa: E402
from puzzle_assistant.qa.solvers import solve_question  # noqa: E402

TEXT = "Ordena las cuentas de menor a mayor resultado"


def fixture(name):
    return cv2.imread(os.path.join(HERE, "fixtures", "questions", name + ".png"))


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class ReadSumsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from puzzle_assistant.ocr import Tesseract
        cls.ocr = Tesseract(Config())

    def read(self, img):
        from puzzle_assistant.qa.screen import locate_question, read_question
        return read_question(img, locate_question(img), self.ocr)

    def test_owner_screenshot_division_and_zero(self):
        # was read as 40-3, 1x2, 2055, 2x4 and "solved" wrongly
        s = self.read(fixture("ordenar_division_open"))
        self.assertEqual(s.question.options, ["40 − 3", "10 × 2", "20 ÷ 5", "2 × 4"])
        self.assertFalse(s.text_unsure)
        self.assertEqual(solve_question(s.question).order,
                         ["20 ÷ 5", "2 × 4", "10 × 2", "40 − 3"])        # 4, 8, 20, 37

    def test_second_round_divisions(self):
        # the game revealed 48 ÷ 12, 33 ÷ 3, 84 ÷ 7, 35 − 6 (was read as + and '847')
        s = self.read(fixture("ordenar_division2_synthetic"))
        self.assertEqual(s.question.options, ["33 ÷ 3", "35 − 6", "84 ÷ 7", "48 ÷ 12"])
        self.assertEqual(solve_question(s.question).order,
                         ["48 ÷ 12", "33 ÷ 3", "84 ÷ 7", "35 − 6"])

    def test_older_recordings_still_read(self):
        s = self.read(fixture("ordenar_sumas_answered"))
        self.assertEqual(s.question.options, ["9 + 12", "21 + 10", "63 − 8", "36 + 10"])
        self.assertEqual(self.read(fixture("pista_open")).question.options, ["79", "69", "70", "71"])
        # words are not touched
        s = self.read(fixture("ordenar_peces_open"))
        self.assertIn("Pulpo", s.question.options)
        self.assertFalse(s.text_unsure)

    def test_doubtful_reading_is_flagged(self):
        img = fixture("ordenar_division_open").copy()
        from puzzle_assistant.qa.screen import locate_question
        bx, by, bw, bh = locate_question(img).all_boxes()[1]
        # a smudge joining '10' and '×' (like the mouse pointer over it)
        cv2.rectangle(img, (bx + 58, by + 14), (bx + 66, by + 16), (240, 240, 240), -1)
        s = self.read(img)
        self.assertTrue(s.text_unsure)


class SafetyTests(unittest.TestCase):
    def test_solver_refuses_sums_without_signs(self):
        q = Question("ORDENAR", TEXT, ["40-3", "1x2", "2055", "2x4"], ORDER)
        self.assertIsNone(solve_question(q))
        q = Question("ORDENAR", TEXT, ["40 − 3", "10 × 2", "20 ÷ 5", "2 × 4"], ORDER)
        self.assertIsNotNone(solve_question(q))

    def test_looks_like_math(self):
        for s in ("2055", "33+3", "84 +7", "1x2", "70"):
            self.assertTrue(looks_like_math(s), s)
        for s in ("Pulpo", "El caballo de Troya", "V", ""):
            self.assertFalse(looks_like_math(s), s)


class MemoryRepairTests(unittest.TestCase):
    def test_old_sum_readings_are_forgotten_and_not_imported(self):
        self.assertTrue(suspect_sum(TEXT, ORDER, ["48 + 12", "33 + 3", "847", "35-6"]))
        self.assertFalse(suspect_sum(TEXT, ORDER, ["9 + 12", "21 + 10", "63 − 8", "36 + 10"]))
        self.assertFalse(suspect_sum(TEXT, ORDER, ["40 − 3", "10 × 2", "20 ÷ 5", "2 × 4"]))
        self.assertFalse(suspect_sum("Ordena los peces", ORDER, ["Pulpo", "Atún"]))
        d = tempfile.mkdtemp()
        old = Memory(os.path.join(d, "old.db"))
        bad = Question("ORDENAR", TEXT, ["48 + 12", "33 + 3", "847", "35-6"], ORDER)
        good = Question("ORDENAR", TEXT, ["9 + 12", "21 + 10", "63 − 8", "36 + 10"], ORDER)
        fish = Question("ORDENAR", "Ordena los peces de menor a mayor precio", ["Pulpo", "Atún"], ORDER)
        for q in (bad, good, fish):
            old.save(q, Answer(ORDER, order=list(q.options), source="game", verified=True))
        new = Memory(os.path.join(d, "new.db"))
        try:
            c = new.merge_from(os.path.join(d, "old.db"))           # Import… skips it
            self.assertEqual((c["new"], c["skipped"]), (2, 1))
            self.assertEqual(old.forget_sum_questions(), 1)         # repair on start
            self.assertEqual(old.stats()["questions"], 2)
            self.assertIsNone(old.lookup(bad))
            self.assertIsNotNone(old.lookup(fish))
        finally:
            old.close()
            new.close()


if __name__ == "__main__":
    unittest.main()
