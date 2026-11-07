"""Overlay layout: numbers per card, fixed while pieces are mounted."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from puzzle_assistant.analyzer import Analysis  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.engine import with_progress  # noqa: E402
from puzzle_assistant.model import Piece, Puzzle  # noqa: E402
from puzzle_assistant.overlay import overlay_items  # noqa: E402
from puzzle_assistant.solver import solve  # noqa: E402

# puzzle_38_touching_digits: C B A D E G, F = caliber 9
SPEC = [("A", "38", "BC"), ("B", None, "C"), ("C", "38", ""), ("D", "38", "C"),
        ("E", "38", "C"), ("F", "9", "BC"), ("G", "38", "C")]
CARDS = [(100 + (i % 3) * 120, 200 + (i // 3) * 60, 118, 58) for i in range(7)]


def locked():
    pz = Puzzle(target_caliber="38")
    for l, c, d in SPEC:
        pz.pieces[l] = Piece(l, "", c, list(d))
    s = solve(pz, 7)
    s.numbering = {l: i + 1 for i, l in enumerate(s.order)}
    return Analysis(pz, s, None, "layout")


def by_card(items):
    out = {}
    for it in items:
        for i, (x, y, w, h) in enumerate(CARDS):
            if x <= it.x <= x + w and y <= it.y <= y + h:
                out["ABCDEFG"[i]] = (it.kind, it.text)
    return out


class OverlayTests(unittest.TestCase):
    def test_numbers_on_cards(self):
        got = by_card(overlay_items(locked(), CARDS))
        self.assertEqual(got["C"], ("next", "1"))
        self.assertEqual(got["B"], ("step", "2"))
        self.assertEqual(got["G"], ("step", "6"))
        self.assertEqual(got["F"], ("skip", "✕"))

    def test_numbers_fixed_while_mounting(self):
        a = with_progress(locked(), [False, True, True, False, False, False, False], Config())
        got = by_card(overlay_items(a, CARDS))
        self.assertEqual(got["C"][0], "done")
        self.assertEqual(got["B"][0], "done")
        self.assertEqual(got["A"], ("next", "3"))   # still 3, not renumbered
        self.assertEqual(got["G"], ("step", "6"))

    def test_probar_when_all_mounted(self):
        a = with_progress(locked(), [True, True, True, True, True, False, True], Config())
        kinds = [it.kind for it in overlay_items(a, CARDS)]
        self.assertIn("probar", kinds)
        self.assertNotIn("step", kinds)

    def test_nothing_clickable_when_unsure(self):
        a = locked()
        a.puzzle.pieces["E"].confidence = 20
        a.solution = solve(a.puzzle, 7)
        kinds = {it.kind for it in overlay_items(a, CARDS)}
        self.assertEqual(kinds, {"doubt"})


if __name__ == "__main__":
    unittest.main()
