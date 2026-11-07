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
