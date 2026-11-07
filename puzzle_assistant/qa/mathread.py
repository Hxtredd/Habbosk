"""Reading short sums on option buttons ("40 − 3", "10 × 2", "20 ÷ 5").

General OCR (Tesseract) hardly knows the ÷ sign and, with text this small
(about 9 px), sometimes drops a digit next to an operator: "20 ÷ 5" was read
as "2055" / "205", "10 × 2" as "1x2". This reader works on the shapes:

  * every character is a column of ink; operator signs are recognised by
    their geometry (÷ = dot over bar over dot, − = one flat bar, = = two bars,
    + and × = a small cross: × has ink in its corners, + does not);
  * the numbers between them are read by Tesseract with digits only, and the
    number of digits must equal the number of characters found there.

It returns None whenever anything doesn't fit, so the caller can refuse to
solve rather than trust a wrong reading.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..vision import prep_for_ocr, to_gray

_WL_DIGITS = ["-c", "tessedit_char_whitelist=0123456789"]
OPS = "+−×÷="

Box = Tuple[int, int, int, int]       # x, y, w, h


def _ink(img: np.ndarray) -> np.ndarray:
    """Bright text on a dark button -> boolean mask."""
    g = to_gray(img).astype(np.int16)
    med, top = int(np.median(g)), int(g.max())
    if top - med < 60:
        return np.zeros(g.shape, bool)
    return g > (med + top) // 2


def _components(mask: np.ndarray) -> List[Box]:
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return [tuple(int(v) for v in stats[i][:4]) for i in range(1, n) if stats[i][4] >= 2]


def _columns(comps: List[Box]) -> List[List[Box]]:
    """Group components that overlap horizontally (÷ is three of them)."""
    cols: List[List[Box]] = []
    for c in sorted(comps):
        if cols:
            x0 = min(b[0] for b in cols[-1])
            x1 = max(b[0] + b[2] for b in cols[-1])
            if c[0] < x1 and c[0] + c[2] > x0:
                cols[-1].append(c)
                continue
        cols.append([c])
    return cols


def _classify(col: List[Box], mask: np.ndarray, H: int) -> str:
    """'d' (digit-sized), an operator from OPS, or '?'."""
    col = sorted(col, key=lambda b: b[1])
    flat = [b for b in col if b[3] <= max(2, 0.3 * H) and b[2] >= 0.35 * H]
    if len(col) == 3 and col[1] in flat and all(b[2] <= 0.4 * H and b[3] <= 0.4 * H
                                               for b in (col[0], col[2])):
        return "÷"
    if len(col) == 2 and len(flat) == 2:
        return "="
    if len(col) != 1:
        return "?"
    x, y, w, h = col[0]
    if col[0] in flat:
        return "−"
    if h >= 0.8 * H:
        return "d"
    if 0.35 * H <= h < 0.8 * H and 0.6 <= w / h <= 1.6:
        m = mask[y:y + h, x:x + w]
        k = max(1, min(w, h) // 4)
        corners = m[:k, :k].sum() + m[:k, -k:].sum() + m[-k:, :k].sum() + m[-k:, -k:].sum()
        mid_row, mid_col = m[h // 2 - (h % 2 == 0):h // 2 + 1].any(axis=0).mean(), \
            m[:, w // 2 - (w % 2 == 0):w // 2 + 1].any(axis=1).mean()
        if corners >= 3:
            return "×"
        if mid_row >= 0.8 and mid_col >= 0.8:
            return "+"
    return "?"


def _digits_in(col: List[Box], H: int) -> int:
    """How many digits one ink column holds (normally 1; '12' can touch)."""
    w = max(b[0] + b[2] for b in col) - min(b[0] for b in col)
    return max(1, round(w / (0.7 * H)))


def read_math(img: np.ndarray, ocr) -> Optional[str]:
    """'20 ÷ 5' style text of a button showing a number or a short sum, or
    None if it isn't one or can't be read with certainty."""
    if img is None or img.size == 0:
        return None
    mask = _ink(img)
    comps = _components(mask)
    if not comps:
        return None
    H = max(b[3] for b in comps)
    if H < 5:
        return None
    # one line of text only, everything within the digit band
    top = min(b[1] for b in comps if b[3] >= 0.8 * H)
    bottom = max(b[1] + b[3] for b in comps if b[3] >= 0.8 * H)
    comps = [b for b in comps if b[1] >= top - 2 and b[1] + b[3] <= bottom + 2]
    cols = _columns(comps)
    kinds = [_classify(c, mask, H) for c in cols]
    if "?" in kinds:
        return None
    # tokens: runs of digit columns, separated by operators
    tokens: List[Tuple[str, List[List[Box]]]] = []
    for k, c in zip(kinds, cols):
        if k == "d":
            if tokens and tokens[-1][0] == "num":
                tokens[-1][1].append(c)
            else:
                tokens.append(("num", [c]))
        else:
            tokens.append((k, [c]))
    if not tokens or tokens[0][0] != "num" or tokens[-1][0] != "num":
        return None
    for i, (k, _) in enumerate(tokens):          # strictly number, op, number, ...
        if (k == "num") != (i % 2 == 0):
            return None
    nums = [t for t in tokens if t[0] == "num"]
    if len(nums) > 4:
        return None
    # read all numbers in one OCR call: blank the operators, keep wide gaps
    clean = img.copy()
    bg = np.median(clean.reshape(-1, clean.shape[-1]) if clean.ndim == 3 else clean, axis=0)
    for k, cs in tokens:
        if k != "num":
            x0 = min(b[0] for b in cs[0]) - 1
            x1 = max(b[0] + b[2] for b in cs[0]) + 1
            clean[:, max(0, x0):x1] = bg
    pieces = []
    for _k, cs in nums:
        x0 = min(b[0] for c in cs for b in c)
        x1 = max(b[0] + b[2] for c in cs for b in c)
        # touching digits ('12') make one wide column: count them by width
        n = sum(_digits_in(c, H) for c in cs)
        pieces.append((x0, x1, n))
    pad = max(3, H // 2)
    y0, y1 = max(0, top - pad), min(img.shape[0], bottom + pad)
    # rebuild a strip with clear spaces between the numbers
    gap = np.full((y1 - y0, 3 * H) + clean.shape[2:], bg, dtype=clean.dtype)
    parts = []
    for x0, x1, _n in pieces:
        parts += [gap, clean[y0:y1, max(0, x0 - 2):x1 + 2]]
    strip = np.concatenate(parts + [gap], axis=1)
    words = ocr.words(prep_for_ocr(strip, scale=4.0), psm=7, extra=_WL_DIGITS)
    found = [re.sub(r"\D", "", w.text) for w in words]
    found = [f for f in found if f]
    if len(found) != len(pieces) or any(len(f) != n for f, (_a, _b, n) in zip(found, pieces)):
        return None                                # a digit lost or added: don't guess
    out = [found[0]]
    for (k, _), f in zip(tokens[1::2], found[1:]):
        out += [k, f]
    return " ".join(out)


_MATHY = re.compile(r"^[\d\s+\-−–x×X*÷:/=().,]+$")


def looks_like_math(raw: str) -> bool:
    """General OCR result of an option that is (probably) a number or a sum."""
    return bool(raw.strip()) and bool(re.search(r"\d", raw)) and bool(_MATHY.match(raw.strip()))
