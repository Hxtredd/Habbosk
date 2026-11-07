"""Tiny-glyph recogniser for the game's small text (5x7 px letters/digits).

Each glyph is cut out by connected components, normalised to a 12x16 grid and
compared (normalised cross-correlation) with templates rendered from many
fonts (glyph_templates.npz). Returns a score and the margin over the runner-up
so doubtful glyphs can be flagged instead of guessed.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

GW, GH = 12, 16
_TEMPLATES: Optional[Tuple[np.ndarray, np.ndarray]] = None
Box = Tuple[int, int, int, int]


def normalize(g: np.ndarray) -> Optional[np.ndarray]:
    g = g.astype(np.float32)
    if g.size == 0 or g.max() <= 0:
        return None
    ys, xs = np.nonzero(g > g.max() * 0.35)
    if len(xs) == 0:
        return None
    g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    g = cv2.resize(g, (GW, GH), interpolation=cv2.INTER_AREA)
    g -= g.mean()
    n = float(np.linalg.norm(g))
    return g / n if n > 0 else None


def _templates() -> Tuple[np.ndarray, np.ndarray]:
    global _TEMPLATES
    if _TEMPLATES is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glyph_templates.npz")
        data = np.load(path)
        t = data["templates"].astype(np.float32).reshape(len(data["labels"]), -1)
        _TEMPLATES = (data["labels"], t)
    return _TEMPLATES


def classify(ink: np.ndarray, charset: str) -> Tuple[str, float, float]:
    """ink: bright-on-dark crop of ONE glyph. Returns (char, score, margin)."""
    v = normalize(ink)
    if v is None:
        return "?", 0.0, 0.0
    labels, t = _templates()
    scores = t @ v.reshape(-1)
    best = {}
    for ch in charset:
        m = labels == ch
        if m.any():
            best[ch] = float(scores[m].max())
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if not ranked:
        return "?", 0.0, 0.0
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    return ranked[0][0], ranked[0][1], ranked[0][1] - second


def components(mask: np.ndarray) -> List[Box]:
    n, _, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return sorted([tuple(int(v) for v in st[i][:4]) for i in range(1, n) if st[i][4] >= 1],
                  key=lambda b: b[0])


def split_touching(box: Box, ink: np.ndarray, pitch: float) -> List[Box]:
    """Split a component that holds several touching characters (e.g. '56')."""
    x, y, w, h = box
    n = int(round(w / pitch)) if pitch > 0 else 1
    if n <= 1:
        return [box]
    col = ink[y:y + h, x:x + w].astype(np.float32).sum(axis=0)
    cuts = [0]
    for i in range(1, n):
        target = int(round(i * w / n))
        lo, hi = max(cuts[-1] + 1, target - 2), min(w - 1, target + 2)
        if hi <= lo:
            continue
        cuts.append(lo + int(np.argmin(col[lo:hi + 1])))
    cuts.append(w)
    return [(x + a, y, b - a, h) for a, b in zip(cuts, cuts[1:]) if b - a > 0]


def crop(ink: np.ndarray, box: Box, pad: int = 1) -> np.ndarray:
    x, y, w, h = box
    return ink[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]


def _segment(ink: np.ndarray, box: Box, a: int, b: int) -> np.ndarray:
    x, y, w, h = box
    return ink[y:y + h, x + a:x + b]


def read_component(ink: np.ndarray, box: Box, charset: str, pitch: float
                   ) -> List[Tuple[str, float, float, Box]]:
    """Recognise one connected component that may hold several touching
    characters (e.g. '38' drawn with the 3 and 8 joined by one pixel).

    Every way of cutting it into the expected number of characters is tried and
    the cut whose pieces look most like real characters wins. A fixed cut
    position would hand a sliver of one digit to its neighbour.
    """
    x, y, w, h = box
    if pitch <= 0 or w <= 1.3 * pitch:
        ch, sc, mg = classify(_segment(ink, box, 0, w), charset)
        return [(ch, sc, mg, box)]
    n = max(2, int(round(w / pitch)))
    lo, hi = max(2, int(0.55 * pitch)), int(1.6 * pitch) + 1
    cache = {}

    def seg(a: int, b: int):
        if (a, b) not in cache:
            cache[(a, b)] = classify(_segment(ink, box, a, b), charset)
        return cache[(a, b)]

    best: Tuple[float, List[Tuple[int, int]]] = (-1e9, [])

    def search(start: int, left: int, acc: float, cuts: List[Tuple[int, int]]):
        nonlocal best
        if left == 1:
            if lo <= w - start <= hi:
                total = acc + seg(start, w)[1]
                if total > best[0]:
                    best = (total, cuts + [(start, w)])
            return
        for end in range(start + lo, min(w, start + hi) + 1):
            search(end, left - 1, acc + seg(start, end)[1], cuts + [(start, end)])

    search(0, n, 0.0, [])
    if not best[1]:  # no valid cut: fall back to equal widths
        step = w / n
        best = (0.0, [(int(round(i * step)), int(round((i + 1) * step))) for i in range(n)])
    out = []
    for a, b in best[1]:
        ch, sc, mg = seg(a, b)
        out.append((ch, sc, mg, (x + a, y, b - a, h)))
    return out
