"""Finds the puzzle on screen by its grid of piece cards.

A grid is accepted only when its cards look like the game's cards (letter
badge + caliber/arrow row), so other boxes on screen (sidebars, shop
shelves...) are ignored. Detection is pure image processing (~15 ms per
1080p frame): no OCR, no dependence on the translucent background or on the
title text, which also appears in the game's task sidebar.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .cardreader import is_mounted, layout_recognised, read_card
from .config import Config
from .vision import Rect, dhash, find_cards, find_grids, hamming, panel_around

Found = Tuple[Rect, List[Rect]]  # panel rect (frame coords), card rects (panel coords)


def _valid(frame: np.ndarray, cards: List[Rect], cfg: Config) -> bool:
    if not cfg.require_layout:
        return True
    readings = [read_card(frame[y:y + h, x:x + w]) for x, y, w, h in cards]
    return layout_recognised(readings)


def _to_panel(frame: np.ndarray, cards: List[Rect]) -> Found:
    px, py, pw, ph = panel_around(cards, frame.shape)
    return (px, py, pw, ph), [(x - px, y - py, w, h) for x, y, w, h in cards]


def search(frame: np.ndarray, gray: np.ndarray, cfg: Config) -> Optional[Found]:
    """Full-frame search."""
    n = cfg.expected_pieces
    for grid in find_grids(gray, min_n=n or 5, max_n=n or 8):
        if _valid(frame, grid, cfg):
            return _to_panel(frame, grid)
    return None


class Locator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.current: Optional[Found] = None
        self._band: Optional[int] = None   # fingerprint of the header strip

    def reset(self) -> None:
        self.current = None
        self._band = None

    @staticmethod
    def _band_hash(frame: np.ndarray, found: Found) -> Optional[int]:
        (x, y, w, h), cards = found
        top = min(c[1] for c in cards)
        if top < 6:
            return None
        return dhash(frame[y:y + top, x:x + w], 16)

    def find(self, frame: np.ndarray, gray: np.ndarray) -> Optional[Found]:
        # 1) cheap re-check where the puzzle was last time. Cards may change
        #    look while the player mounts them, so the header strip above the
        #    cards ('Fusil · calibre 5.56') unchanged also counts as present.
        if self.current is not None:
            (x, y, w, h), old = self.current
            if x + w <= frame.shape[1] and y + h <= frame.shape[0]:
                cards = find_cards(gray[y:y + h, x:x + w])
                # Same puzzle if every visible card is where a known card was.
                # Mounted cards change look and may drop out of the detection,
                # so fewer cards than before still counts.
                def near(a, b):
                    return abs(a[0] - b[0]) <= 3 and abs(a[1] - b[1]) <= 3
                if cards and len(cards) <= len(old) and all(
                        any(near(a, b) for b in old) for a in cards):
                    panel = frame[y:y + h, x:x + w]
                    missing = [b for b in old if not any(near(a, b) for a in cards)]
                    if all(is_mounted(panel[by:by + bh, bx:bx + bw])
                           for bx, by, bw, bh in missing):
                        return self.current
                if not cards:  # grid not visible as such (cards being mounted?)
                    band = self._band_hash(frame, self.current)
                    if band is not None and self._band is not None and \
                            hamming(band, self._band) <= 20:
                        return self.current
        # 2) whole frame
        self.current = search(frame, gray, self.cfg)
        self._band = self._band_hash(frame, self.current) if self.current else None
        return self.current
