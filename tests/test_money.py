"""'Cuenta el dinero': replays of two real rounds (tests/fixtures/money_*.npz,
the puzzle area only, 10 frames per second)."""
import os
import sys
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.locate import search  # noqa: E402
from puzzle_assistant.money import (MoneyTracker, locate_money, parse_vistos,  # noqa: E402
                                    read_legend)
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.vision import prep_for_ocr, to_gray  # noqa: E402

ROUNDS = {
    "money_coins_125": dict(items=[25, 5, 10, 25, 5, 10, 25, 10, 10], total=125,
                            legend=[5, 10, 25], expected=9),
    "money_bills_390": dict(items=[100, 20, 50, 100, 20, 50, 50], total=390,
                            legend=[20, 50, 100], expected=7),   # game accepted 390
}


def load(name):
    d = np.load(os.path.join(HERE, "fixtures", name + ".npz"))
    return d["frames"], d["t"], tuple(d["strip"]), tuple(d["band"])


def replay(name, ocr=None):
    frames, times, (sx, sy, sw, sh), (bx, by, bw, bh) = load(name)
    tr = MoneyTracker()
    for k, (f, t) in enumerate(zip(frames, times)):
        tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        if k % 4 == 0:
            band = f[by:by + bh, bx:bx + bw]
            tr.set_legend(read_legend(band))
            if ocr is not None:
                text = ocr.text(prep_for_ocr(band[:, :int(bw * 0.45)], 3.0), psm=7)
                tr.set_vistos(parse_vistos(text))
    return tr.state(float(times[-1]) + 1.0)


class MoneyTests(unittest.TestCase):
    def test_items_and_total(self):
        for name, want in ROUNDS.items():
            st = replay(name)
            self.assertEqual([v for v, _ in st.items], want["items"], name)
            self.assertTrue(all(s for _, s in st.items), name)
            self.assertEqual(st.total, want["total"], name)
            self.assertEqual(st.legend, want["legend"], name)
            self.assertFalse(st.done, "without the Vistos counter it must not claim done")

    def test_locate(self):
        for name in ROUNDS:
            img = cv2.imread(os.path.join(HERE, "fixtures", name + "_panel.png"))
            p = locate_money(to_gray(img))
            self.assertIsNotNone(p, name)
            self.assertIsNone(search(img, to_gray(img), Config()), "not a weapon puzzle")
        for fx in ("puzzle_8_pieces.png", "puzzle_7_pieces.png"):
            img = cv2.imread(os.path.join(HERE, "fixtures", fx))
            self.assertIsNone(locate_money(to_gray(img)), fx)

    def test_parse_vistos(self):
        self.assertEqual(parse_vistos("Vistos 3/9"), (3, 9))
        self.assertEqual(parse_vistos("vistos 7 / 7"), (7, 7))
        self.assertIsNone(parse_vistos("Vistos"))

    def test_missed_item_is_not_verified(self):
        frames, times, (sx, sy, sw, sh), _ = load("money_bills_390")
        tr = MoneyTracker()
        for k, (f, t) in enumerate(zip(frames, times)):
            if 30 <= k < 52:            # app "blind" for 2.2 s: one bill is missed
                continue
            tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        tr.set_vistos((7, 7))
        st = tr.state(float(times[-1]) + 1.0)
        self.assertFalse(st.verified)


    def test_frozen_after_done(self):
        frames, times, (sx, sy, sw, sh), _ = load("money_bills_390")
        tr = MoneyTracker()
        for f, t in zip(frames, times):
            tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        tr.set_vistos((7, 7))
        end = float(times[-1]) + 1.0
        st = tr.state(end)
        self.assertTrue(st.done and st.verified)
        # the puzzle closes: whatever is behind the strip must not be counted
        for k in range(20):
            tr.update(frames[k][sy:sy + sh, sx:sx + sw], end + 0.1 * k)
        tr.set_vistos((1, 9))
        self.assertEqual(tr.state(end + 3).total, 390)


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class MoneyVerifiedTests(unittest.TestCase):
    def test_verified_with_vistos(self):
        from puzzle_assistant.ocr import Tesseract
        ocr = Tesseract(Config())
        for name, want in ROUNDS.items():
            st = replay(name, ocr)
            self.assertEqual((st.seen, st.expected), (want["expected"], want["expected"]), name)
            self.assertTrue(st.done and st.verified, (name, st.problems))
            self.assertEqual(st.total, want["total"])


if __name__ == "__main__":
    unittest.main()
