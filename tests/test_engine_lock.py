"""The verified answer must survive cards turning green, and a new puzzle
must replace it (no OCR needed)."""
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.analyzer import card_hashes  # noqa: E402
from puzzle_assistant.cardreader import is_mounted  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.engine import header_band, same_puzzle  # noqa: E402
from puzzle_assistant.locate import Locator, search  # noqa: E402
from puzzle_assistant.vision import to_gray  # noqa: E402


def state(img, locator=None):
    if locator is None:
        locator = Locator(Config())
    (x, y, w, h), cards = locator.find(img, to_gray(img))
    panel = img[y:y + h, x:x + w]
    mounted = [is_mounted(panel[cy:cy + ch, cx:cx + cw]) for cx, cy, cw, ch in cards]
    return panel, cards, card_hashes(panel, cards), header_band(panel, cards), mounted


def mount(img, panel_cards, idx):
    """Fake the game's 'mounted' look on card idx: green outline, dim text."""
    out = img.copy()
    (x, y, w, h), cards = panel_cards
    cx, cy, cw, ch = cards[idx]
    card = out[y + cy:y + cy + ch, x + cx:x + cx + cw]
    card[:] = (card * 0.55).astype(card.dtype)
    cv2.rectangle(card, (0, 0), (cw - 1, ch - 1), (90, 160, 60), 2)
    return out


class LockTests(unittest.TestCase):
    def test_mounting_keeps_lock_new_puzzle_breaks_it(self):
        img = cv2.imread(os.path.join(HERE, "fixtures", "puzzle_38_touching_digits.png"))
        found = search(img, to_gray(img), Config())
        loc = Locator(Config())                            # as the live engine does
        _, _, h0, b0, m0 = state(img, loc)
        self.assertFalse(any(m0))
        step = mount(mount(img, found, 2), found, 1)      # C then B mounted
        _, _, h1, b1, m1 = state(step, loc)
        self.assertEqual(m1, [False, True, True, False, False, False, False])
        self.assertTrue(same_puzzle(h0, b0, h1, b1, m1))
        other = cv2.imread(os.path.join(HERE, "fixtures", "puzzle_38_dont_click_D.png"))
        _, _, h2, b2, m2 = state(other, loc)
        self.assertFalse(same_puzzle(h0, b0, h2, b2, m2))


if __name__ == "__main__":
    unittest.main()
