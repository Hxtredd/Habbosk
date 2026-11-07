"""Tests on real puzzle panels from the game (tests/fixtures).

The card reading (letters, calibers, arrows) needs no OCR; the full pipeline
(header: target caliber) is skipped if Tesseract is not installed.
"""
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from puzzle_assistant.cardreader import read_card  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.locate import search  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.vision import to_gray  # noqa: E402

# cards: (caliber, arrow letters) for every UNMOUNTED card; None = mounted
CASES = {
    "puzzle_8_pieces.png": dict(
        cards=[("5.56", "BH"), ("5.56", ""), (None, "B"), (None, "EH"), ("5.56", "B"),
               ("5.56", "EH"), ("7.62", "B"), ("5.56", "E")],
        target="5.56", excluded=["G"], done=[], remaining="BCEHADF"),
    "puzzle_7_pieces.png": dict(
        cards=[("9", "G"), ("9", "F"), ("9", "D"), (None, "G"), ("38", "D"), ("9", "G"), ("9", "")],
        target="9", excluded=["E"], done=[], remaining="GADCFB"),
    "puzzle_38_dont_click_D.png": dict(
        cards=[("38", "F"), (None, "C"), ("38", ""), ("9", "F"), ("38", "B"), ("38", "C"),
               ("38", "AE")],
        target="38", excluded=["D"], done=[], remaining="CBEFAG"),
    "puzzle_38_touching_digits.png": dict(   # the 3 and 8 touch: must not read "30"
        cards=[("38", "BC"), (None, "C"), ("38", ""), ("38", "C"), ("38", "C"), ("9", "BC"),
               ("38", "C")],
        target="38", excluded=["F"], done=[], remaining="CBADEG"),
    "puzzle_12_three_mounted.png": dict(     # B, E, G already mounted (green, ✓)
        cards=[("20", "G"), None, ("20", "D"), (None, "BE"), None, ("12", "D"), None, (None, "D")],
        target="12", excluded=["A", "C"], done=["B", "E", "G"], remaining="DFH"),
}


def load(name):
    img = cv2.imread(os.path.join(HERE, "fixtures", name))
    found = search(img, to_gray(img), Config())
    assert found is not None, f"puzzle not found in {name}"
    (x, y, w, h), cards = found
    return img[y:y + h, x:x + w], cards


class CardReaderTests(unittest.TestCase):
    def test_cards(self):
        for name, case in CASES.items():
            panel, cards = load(name)
            self.assertEqual(len(cards), len(case["cards"]), name)
            for i, ((x, y, w, h), want) in enumerate(zip(cards, case["cards"])):
                r = read_card(panel[y:y + h, x:x + w])
                self.assertEqual(r.badge, "ABCDEFGH"[i], f"{name} card {i}")
                self.assertEqual(r.mounted, want is None, f"{name} card {i} mounted")
                if want is None:
                    continue
                cal, deps = want
                self.assertEqual(r.caliber, cal, f"{name} card {i}")
                self.assertEqual("".join(r.deps), deps, f"{name} card {i}")
                self.assertGreaterEqual(r.confidence, 90, f"{name} card {i} {r.notes}")


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class FullPipelineTests(unittest.TestCase):
    def test_solutions(self):
        from puzzle_assistant.analyzer import Analyzer
        from puzzle_assistant.ocr import Tesseract
        cfg = Config()
        analyzer = Analyzer(cfg, Tesseract(cfg))
        for name, case in CASES.items():
            panel, cards = load(name)
            a = analyzer.analyze_panel(panel, cards)
            self.assertEqual(a.puzzle.target_caliber, case["target"], name)
            self.assertEqual(a.solution.excluded, case["excluded"], name)
            self.assertEqual(a.solution.done, case["done"], name)
            self.assertEqual("".join(a.solution.remaining), case["remaining"], name)
            self.assertTrue(a.solution.certain, (name, a.solution.problems, a.solution.warnings))


if __name__ == "__main__":
    unittest.main()
