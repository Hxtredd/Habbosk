"""'Cuenta el dinero' puzzle: add up the coins and bills that slide past.

    Cuenta el dinero
    Cada billete y moneda pasa una sola vez. Súmalos y escribe el total.
    [  (10)      [ 20 ]       [100] ]   <- strip: items move left -> right
    Vistos 3/7                 5 · 10 · 20 · 100
    [                               ]   <- input box (the player types)
    [ 1 ][ 2 ][ 3 ] ... keypad

Detection: the 3-column keypad grid + the input box above it. The strip and
the "Vistos" line sit at fixed distances above the input box, scaled by the
keypad's button height.

Each item is tracked from frame to frame (it only moves right) so it is
counted exactly once; its printed value is read many times while it crosses
and the majority wins. The game's own "Vistos N/M" counter (it counts an item
when it appears) and the value legend are used to verify the result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .glyphs import components, read_component
from .vision import Rect, find_grids, rect_candidates

GOOD_SCORE = 0.6


# ---------------------------------------------------------------------------
# Locating the puzzle
# ---------------------------------------------------------------------------
@dataclass
class MoneyPanel:
    keypad: Rect
    input: Rect
    strip: Rect
    band: Rect          # the "Vistos 3/7 .... 5 · 10 · 25" line

    def area(self) -> Rect:
        """Smallest frame area holding strip, band and input box."""
        x0 = min(self.strip[0], self.input[0]) - 4
        y0 = self.strip[1] - 4
        x1 = max(self.strip[0] + self.strip[2], self.input[0] + self.input[2],
                 self.band[0] + self.band[2]) + 4
        y1 = self.input[1] + self.input[3] + 4
        return (x0, y0, x1 - x0, y1 - y0)


def _cluster(vals: List[float], tol: float) -> List[float]:
    out: List[List[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.mean(c)) for c in out]


def locate_money(gray: np.ndarray) -> Optional[MoneyPanel]:
    H, W = gray.shape[:2]
    for grid in find_grids(gray, min_n=9, max_n=12):
        bw = int(np.median([b[2] for b in grid]))
        bh = int(np.median([b[3] for b in grid]))
        cols = _cluster([b[0] for b in grid], bw * 0.3)
        rows = _cluster([b[1] for b in grid], bh * 0.3)
        if len(cols) != 3 or not 3 <= len(rows) <= 4:
            continue
        x0 = min(b[0] for b in grid)
        x1 = max(b[0] + b[2] for b in grid)
        y0 = min(b[1] for b in grid)
        kw = x1 - x0
        # the input box: as wide as the keypad, just above it
        wy0 = max(0, int(y0 - 3 * bh))
        wx0 = max(0, x0 - 20)
        win = gray[wy0:y0, wx0:min(W, x1 + 20)]
        if win.size == 0:
            continue
        boxes = [(x + wx0, y + wy0, w, h) for x, y, w, h in rect_candidates(win, 200, win.size)]
        boxes = [b for b in boxes if abs(b[2] - kw) <= 0.08 * kw and 0.6 * bh <= b[3] <= 1.6 * bh]
        if not boxes:
            continue
        inp = max(boxes, key=lambda b: b[1])                 # the lowest one
        it = inp[1]
        strip_top = int(it - 2.875 * bh)
        strip_bot = int(it - 1.06 * bh)
        if strip_top < 0:
            continue
        strip = (x0, strip_top, kw, strip_bot - strip_top)
        band = (x0, strip_bot + 1, int(kw * 1.02), max(4, it - strip_bot - 2))
        return MoneyPanel((x0, y0, kw, max(b[1] + b[3] for b in grid) - y0), inp, strip, band)
    return None


# ---------------------------------------------------------------------------
# Reading numbers (dark digits on a coin / bill, grey digits in the legend)
# ---------------------------------------------------------------------------
def _merge_columns(boxes):
    out = []
    for b in sorted(boxes, key=lambda b: b[0]):
        if out and b[0] < out[-1][0] + out[-1][2] - 1:   # x-ranges overlap: same glyph
            a = out[-1]
            x0, y0 = min(a[0], b[0]), min(a[1], b[1])
            x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
            out[-1] = (x0, y0, x1 - x0, y1 - y0)
        else:
            out.append(b)
    return out


def read_number_text(ink: np.ndarray, rel: float = 0.35,
                     drop_edges: bool = False) -> Tuple[str, float]:
    """Digits (and '.' separators) from a bright-ink image. Returns (text, worst score).
    drop_edges: ignore anything touching the image border (window frames, edges)."""
    if ink.size == 0 or ink.max() < 25:
        return "", 0.0
    m = ink > max(25, rel * float(ink.max()))
    cs = _merge_columns(components(m))
    cs = [b for b in cs if b[2] * b[3] >= 2]
    if drop_edges:
        Hh, Ww = ink.shape[:2]
        cs = [b for b in cs if b[0] > 0 and b[1] > 0 and b[0] + b[2] < Ww and b[1] + b[3] < Hh]
        if cs:  # border lines are much taller than the digits
            med = float(np.median([b[3] for b in cs]))
            cs = [b for b in cs if b[3] <= 1.6 * med]
    if not cs:
        return "", 0.0
    H = max(b[3] for b in cs)
    out, prev, scores = "", None, []
    for b in cs:
        if prev is not None and b[0] - (prev[0] + prev[2]) > 0.7 * H:
            out += " "
        prev = b
        if b[3] < 0.5 * H:
            out += "."
            continue
        for ch, sc, _, _ in read_component(ink, b, "0123456789", H * 0.8):
            out += ch
            scores.append(sc)
    return out, (min(scores) if scores else 0.0)


def read_item(item: np.ndarray) -> Tuple[str, float]:
    """Value printed on one coin or bill (dark digits on a light face)."""
    g = cv2.cvtColor(item, cv2.COLOR_BGR2GRAY).astype(np.int16)
    h, w = g.shape
    yy, xx = np.mgrid[:h, :w]
    inner = ((yy - h / 2) / (0.36 * h)) ** 2 + ((xx - w / 2) / (0.36 * w)) ** 2 < 1
    if inner.sum() < 20:
        return "", 0.0
    face = int(np.median(g[inner]))
    ink = np.clip(face - g, 0, 255).astype(np.uint8)
    ink[~inner] = 0
    text, score = read_number_text(ink, rel=0.45)
    text = text.replace(" ", "")
    return (text, score) if text.isdigit() else ("", 0.0)


def read_legend(band: np.ndarray) -> List[int]:
    """'5 · 10 · 20 · 100' (right part of the band) -> [5, 10, 20, 100]."""
    w = band.shape[1]
    part = band[:, int(w * 0.5):]
    mx = part.max(axis=2).astype(np.int16) if part.ndim == 3 else part.astype(np.int16)
    ink = np.clip(mx - int(np.median(mx)), 0, 255).astype(np.uint8)
    text, score = read_number_text(ink, drop_edges=True)
    if score < GOOD_SCORE:
        return []
    nums = [int(t) for t in re.split(r"[ .]+", text) if t.isdigit()]
    return nums if 2 <= len(nums) <= 8 else []


VISTOS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def parse_vistos(text: str) -> Optional[Tuple[int, int]]:
    m = VISTOS_RE.search(text.replace("|", "/").replace("l", "1"))
    if not m:
        return None
    seen, total = int(m.group(1)), int(m.group(2))
    return (seen, total) if 0 <= seen <= total <= 50 else None


# ---------------------------------------------------------------------------
# Tracking items across frames
# ---------------------------------------------------------------------------
def strip_items(strip: np.ndarray) -> List[Rect]:
    mx = strip.max(axis=2).astype(np.int16)
    bg = int(np.median(mx))
    m = ((mx - bg) > 60).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    h = strip.shape[0]
    return [tuple(int(v) for v in st[i][:4]) for i in range(1, n)
            if st[i][4] > 120 and st[i][3] >= 0.45 * h]


@dataclass
class Track:
    first_t: float
    last_t: float
    cx: float
    w: int
    reads: List[Tuple[str, float]] = field(default_factory=list)

    def value(self) -> Tuple[Optional[int], float, int]:
        """(majority value, agreement 0..1, number of good reads)"""
        good = [t for t, s in self.reads if t and s >= GOOD_SCORE]
        if not good:
            return None, 0.0, 0
        best = max(set(good), key=good.count)
        return int(best), good.count(best) / len(good), len(good)


@dataclass
class MoneyState:
    items: List[Tuple[Optional[int], bool]]   # (value, sure) in passing order
    total: int
    counted: int
    on_strip: int
    seen: Optional[int]
    expected: Optional[int]
    legend: List[int]
    done: bool
    verified: bool
    problems: List[str]


class MoneyTracker:
    LOST_AFTER = 0.35     # s without a sighting: the item has left the strip

    def __init__(self):
        self.frozen = False   # set once the round is complete: nothing can change it
        self.tracks: List[Track] = []
        self.vistos: Optional[Tuple[int, int]] = None
        self.legend: List[int] = []

    def update(self, strip: np.ndarray, t: float) -> None:
        if self.frozen:
            return
        W = strip.shape[1]
        live = [tr for tr in self.tracks if t - tr.last_t <= self.LOST_AFTER]
        used = set()
        for x, y, w, h in sorted(strip_items(strip), key=lambda b: -b[0]):
            cx = x + w / 2
            best, best_d = None, 1e9
            for tr in live:
                if id(tr) in used:
                    continue
                dt = max(1e-3, t - tr.last_t)
                d = cx - tr.cx
                if -0.25 * max(w, tr.w) <= d <= 0.6 * max(w, tr.w) + 500 * dt and abs(d) < best_d:
                    best, best_d = tr, abs(d)
            if best is None:
                best = Track(t, t, cx, w)
                self.tracks.append(best)
            used.add(id(best))
            best.last_t, best.cx, best.w = t, cx, max(best.w, w)
            if x > 1 and x + w < W - 1:                       # fully visible
                best.reads.append(read_item(strip[y:y + h, x:x + w]))

    def set_vistos(self, v: Optional[Tuple[int, int]]) -> None:
        if v is not None and not self.frozen:
            self.vistos = v

    def set_legend(self, legend: List[int]) -> None:
        if legend:
            self.legend = legend

    def state(self, t: float) -> MoneyState:
        if self.frozen:
            return self._final
        problems: List[str] = []
        items: List[Tuple[Optional[int], bool]] = []
        total = 0
        tracks = [tr for tr in self.tracks if tr.reads or t - tr.last_t <= self.LOST_AFTER]
        tracks.sort(key=lambda tr: tr.first_t)
        for i, tr in enumerate(tracks):
            val, agree, n = tr.value()
            sure = val is not None and agree >= 0.8 and n >= 3
            if val is not None and self.legend and val not in self.legend:
                sure = False
                problems.append(f"item {i + 1} read as {val}, not in {self.legend}")
            elif val is not None and not sure:
                problems.append(f"item {i + 1} unclear (read {val})")
            items.append((val, sure))
            total += val or 0
        on_strip = sum(1 for tr in self.tracks if t - tr.last_t <= self.LOST_AFTER)
        seen, expected = self.vistos if self.vistos else (None, None)
        done = expected is not None and seen == expected and on_strip == 0 and len(items) > 0
        if done and len(items) != expected:
            problems.append(f"counted {len(items)} items but the game says {expected}")
        if any(v is None for v, _ in items):
            problems.append("an item could not be read")
        verified = done and not problems and all(s for _, s in items)
        st = MoneyState(items, total, len(items), on_strip, seen, expected, list(self.legend),
                        done, verified, problems)
        if done:  # the round is over: freeze the answer until the puzzle closes
            self.frozen, self._final = True, st
        return st
