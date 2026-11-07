"""Run with:  python -m unittest discover -s tests   (no OCR needed)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from puzzle_assistant.model import Piece, Puzzle  # noqa: E402
from puzzle_assistant.ocr import Word  # noqa: E402
from puzzle_assistant.parser import parse_card, parse_header  # noqa: E402
from puzzle_assistant.solver import normalize_caliber, solve  # noqa: E402


def make(target, spec):
    p = Puzzle(target_caliber=target)
    for letter, cal, deps in spec:
        p.pieces[letter] = Piece(letter=letter, caliber=cal, deps=list(deps))
    return p


def valid_order(order, puzzle, excluded):
    pos = {l: i for i, l in enumerate(order)}
    for l in order:
        for d in puzzle.pieces[l].deps:
            if d not in excluded and pos[d] > pos[l]:
                return False
    return True


class SolverTests(unittest.TestCase):
    def test_example_from_spec(self):
        pz = make("12", [("A", None, "H"), ("B", None, "G"), ("C", None, "H"),
                         ("D", None, "CH"), ("E", None, "G"), ("F", None, "H"),
                         ("G", None, "C"), ("H", None, "")])
        s = solve(pz)
        self.assertTrue(s.ok and s.certain, s.problems + s.warnings)
        self.assertEqual(len(s.order), 8)
        self.assertTrue(valid_order(s.order, pz, set()))
        self.assertEqual(s.order[0], "H")

    def test_excludes_other_caliber(self):
        pz = make("12", [("A", "12", ""), ("B", "9", ""), ("C", None, "A"),
                         ("D", "12", "C"), ("E", "9", ""), ("F", None, ""),
                         ("G", "12", "F"), ("H", None, "G")])
        s = solve(pz)
        self.assertEqual(s.excluded, ["B", "E"])
        self.assertNotIn("B", s.order)
        self.assertTrue(valid_order(s.order, pz, {"B", "E"}))
        self.assertTrue(s.certain)

    def test_cycle_detected(self):
        pz = make("12", [("A", None, "B"), ("B", None, "C"), ("C", None, "A"),
                         ("D", None, ""), ("E", None, ""), ("F", None, ""),
                         ("G", None, ""), ("H", None, "")])
        s = solve(pz)
        self.assertFalse(s.ok)
        self.assertTrue(any("Circular" in p for p in s.problems))

    def test_missing_piece_and_target(self):
        pz = make(None, [("A", None, "")])
        s = solve(pz)
        self.assertFalse(s.ok)
        self.assertTrue(any("Missing" in p for p in s.problems))
        self.assertTrue(any("Target" in p for p in s.problems))

    def test_dep_on_excluded_is_flagged(self):
        pz = make("12", [("A", "9", ""), ("B", None, "A"), ("C", None, ""), ("D", None, ""),
                         ("E", None, ""), ("F", None, ""), ("G", None, ""), ("H", None, "")])
        s = solve(pz)
        self.assertFalse(s.ok)

    def test_low_confidence_is_not_certain(self):
        pz = make("12", [(l, None, "") for l in "ABCDEFGH"])
        pz.pieces["C"].confidence = 20
        s = solve(pz)
        self.assertTrue(s.ok)
        self.assertFalse(s.certain)

    def test_mounted_pieces_are_done(self):
        # puzzle_12_three_mounted: B, E, G mounted; their faded cards may be misread
        pz = make("12", [("A", "20", "G"), ("B", "12", "G"), ("C", "20", "D"), ("D", None, "BE"),
                         ("E", None, "G"), ("F", "12", "D"), ("G", "12", ""), ("H", None, "D")])
        for l in "BEG":
            pz.pieces[l].mounted = True
        pz.pieces["E"].deps = ["E"]          # garbage reading of a faded card
        pz.pieces["E"].confidence = 0
        s = solve(pz)
        self.assertTrue(s.certain, s.problems + s.warnings)
        self.assertEqual(s.done, ["B", "E", "G"])
        self.assertEqual(s.remaining, ["D", "F", "H"])
        self.assertEqual(s.excluded, ["A", "C"])

    def test_mounted_too_early_is_flagged(self):
        pz = make("12", [(l, None, "") for l in "ABCDEFGH"])
        pz.pieces["B"].deps = ["A"]
        pz.pieces["B"].mounted = True        # game shows B done but A is not
        s = solve(pz)
        self.assertFalse(s.certain)
        self.assertIn("B", s.doubtful)

    def test_normalize(self):
        self.assertEqual(normalize_caliber("Cal. 12"), "12")
        self.assertEqual(normalize_caliber("9mm"), "9")
        self.assertEqual(normalize_caliber("7,62"), "7.62")
        self.assertEqual(normalize_caliber(".45"), "45")


def W(text, line=0, x=0, h=20, conf=95):
    return Word(text, conf, x, line * 30, len(text) * 10, h, line)


class ParserTests(unittest.TestCase):
    def test_card_with_arrow(self):
        p = parse_card([W("D", 0, h=40), W("Pasador", 1), W("Cal.", 2), W("12", 2, 50),
                        W("<-", 3), W("C,H", 3, 40)])
        self.assertEqual((p.letter, p.caliber, p.deps), ("D", "12", ["C", "H"]))
        self.assertEqual(p.name, "Pasador")

    def test_card_misread_arrow(self):
        p = parse_card([W("A", 0, h=40), W("Culata", 1), W("“e", 2), W("H", 2, 40)])
        self.assertEqual((p.letter, p.deps), ("A", ["H"]))

    def test_card_glued_arrow(self):
        p = parse_card([W("B", 0, h=40), W("Gatillo", 1), W("eG", 2)])
        self.assertEqual((p.letter, p.deps), ("B", ["G"]))

    def test_unexplained_token_flagged(self):
        p = parse_card([W("G", 0, h=40), W("Armazón", 1), W("x7", 2)])
        self.assertTrue(p.notes)

    def test_header(self):
        words = [W(t) for t in "Montar un arma 0:27 Escopeta calibre 12".split()]
        target, weapon, notes, _ = parse_header(words)
        self.assertEqual((target, weapon), ("12", "Escopeta"))


if __name__ == "__main__":
    unittest.main()
