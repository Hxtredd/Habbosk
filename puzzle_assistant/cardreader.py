"""Reader for the habbosk "Montar un arma" piece card layout.

    +--------------------------------------+
    | [A]  Cerrojo                         |   [A]   letter badge (teal)
    |      5.56              ↳ B H         |   5.56  caliber ('–' = none)
    +--------------------------------------+   ↳ B H pieces that must be mounted first

Calibers and dependency letters are tiny (about 5x7 px at 100 % zoom) so they
are recognised with glyph templates (glyphs.py) instead of OCR. Every glyph
carries a score; anything doubtful is reported, never silently guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .glyphs import Box, classify, components, crop, read_component

GOOD_SCORE = 0.62     # correlation needed to accept a glyph
GOOD_MARGIN = 0.07    # lead over the second-best character


@dataclass
class CardReading:
    ok: bool                              # layout recognised (badge + text rows)
    badge: Optional[str] = None           # letter read from the badge
    caliber: Optional[str] = None         # None = neutral ('–')
    deps: List[str] = field(default_factory=list)
    confidence: float = 100.0
    notes: List[str] = field(default_factory=list)
    name_box: Optional[Box] = None        # where the name is (for optional OCR)
    mounted: bool = False                 # green border + ✓: already mounted
    debug: str = ""


def _ink(card: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Brightness above the card background (colour-independent) and a mask."""
    mx = card.max(axis=2).astype(np.int16) if card.ndim == 3 else card.astype(np.int16)
    bg = int(np.median(mx))
    hi = int(np.percentile(mx, 99.5))
    ink = np.clip(mx - bg, 0, 255).astype(np.uint8)
    thr = max(40, int(0.40 * (hi - bg)))
    return ink, ink > thr


def _zone_mask(ink: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """Threshold one zone on its own: mounted cards draw their text dimmed, so
    a threshold taken from the whole card (with its bright name) is too high."""
    vals = ink[zone]
    if vals.size == 0:
        return np.zeros_like(zone)
    hi = float(np.percentile(vals, 99.5))
    thr = max(25.0, 0.42 * hi)
    return zone & (ink > thr)


def is_mounted(card: np.ndarray) -> bool:
    """The game draws a green outline (and a ✓) around mounted pieces."""
    if card.ndim != 3 or card.shape[0] < 10 or card.shape[1] < 10:
        return False
    c = card.astype(np.int16)
    ring = np.concatenate([c[1:3, :].reshape(-1, 3), c[-3:-1, :].reshape(-1, 3),
                           c[:, 1:3].reshape(-1, 3), c[:, -3:-1].reshape(-1, 3)])
    green_excess = ring[:, 1] - np.maximum(ring[:, 0], ring[:, 2])
    return float(np.percentile(green_excess, 75)) >= 12.0


def _join(a: Box, b: Box) -> Box:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _split_arrow(boxes: List[Box], H: int) -> List[Box]:
    """[arrow, letter, letter...] from the components of the arrow group.
    The arrow may be broken into pieces: they are joined only until the arrow
    is arrow-sized, so a letter drawn close to it is never swallowed. Letters
    broken into pieces (dimmed, mounted cards) are re-joined the same way."""
    boxes = sorted(boxes, key=lambda b: b[0])
    if not boxes:
        return []
    gap = max(1.0, 0.3 * H)
    out: List[Box] = [boxes[0]]
    for b in boxes[1:]:
        cur = out[-1]
        close = b[0] - (cur[0] + cur[2]) <= gap
        limit = 1.15 * H if len(out) == 1 else 0.85 * H   # arrow / letter width
        if close and cur[2] < limit and _join(cur, b)[2] <= (1.7 * H if len(out) == 1 else H):
            out[-1] = _join(cur, b)
        else:
            out.append(b)
    return out


def _accept(score: float, margin: float) -> bool:
    return score >= GOOD_SCORE and margin >= GOOD_MARGIN


def _conf(score: float, margin: float) -> float:
    return 95.0 if _accept(score, margin) else 35.0


def read_card(card: np.ndarray) -> CardReading:
    h, w = card.shape[:2]
    r = CardReading(ok=False)
    if h < 20 or w < 40:
        r.notes.append("card too small")
        return r
    ink, mask = _ink(card)
    r.mounted = is_mounted(card)
    b = max(2, int(round(min(h, w) * 0.06)))       # ignore the card's outline
    inner = np.zeros(mask.shape, dtype=bool)
    inner[b:-b, b:-b] = True
    mask &= inner
    confs: List[float] = []

    # ---------------- badge letter (left part) -------------------------------
    left = mask.copy()
    left[:, int(w * 0.30):] = False
    comps = [c for c in components(left) if c[3] >= h * 0.08]
    if comps:
        bx = max(comps, key=lambda c: c[3])
        ch, sc, mg = classify(crop(ink, bx, pad=0), "ABCDEFGH")
        if _accept(sc, mg):
            r.badge = ch
        r.debug += f"badge={ch}({sc:.2f},{mg:.2f}) "

    # ---------------- name row (top half, right of badge) -------------------
    top = mask.copy()
    top[int(h * 0.55):, :] = False
    top[:, :int(w * 0.25)] = False
    tc = components(top)
    if tc:
        x0 = min(c[0] for c in tc)
        y0 = min(c[1] for c in tc)
        x1 = max(c[0] + c[2] for c in tc)
        y1 = max(c[1] + c[3] for c in tc)
        r.name_box = (x0, y0, x1 - x0, y1 - y0)

    # ---------------- bottom row: caliber .... ↳ letters --------------------
    zone = inner.copy()
    zone[:int(h * 0.50), :] = False
    zone[:, :int(w * 0.25)] = False
    bot = _zone_mask(ink, zone)
    bc = [c for c in components(bot) if c[2] * c[3] >= 2]
    if not bc:
        r.notes.append("no caliber/arrow row found")
        return r
    H = max(c[3] for c in bc)                      # character height
    groups: List[List[Box]] = [[bc[0]]]
    for c in bc[1:]:
        prev = groups[-1][-1]
        if c[0] - (prev[0] + prev[2]) > 1.8 * H:
            groups.append([c])
        else:
            groups[-1].append(c)

    def is_arrow(c: Box) -> bool:
        return c[2] >= 1.15 * H and c[3] >= 0.6 * H

    dep_group: Optional[List[Box]] = None
    cal_group: Optional[List[Box]] = None
    # The arrow glyph is drawn thin and may break into pieces: merge pieces
    # separated by tiny gaps before deciding whether the last group starts
    # with an arrow.
    merged = _split_arrow(groups[-1], H)
    if merged and is_arrow(merged[0]) and merged[0][0] > w * 0.45:
        groups.pop()
        dep_group = merged
    if groups:
        cal_group = groups[0]
        if len(groups) > 1:
            r.notes.append("unexpected extra text on the caliber row")

    # caliber
    if cal_group:
        tall = [c for c in cal_group if c[3] >= 0.5 * H]
        if not tall:
            r.caliber = None                       # only a dash: neutral piece
        else:
            pitch = float(np.median([c[3] for c in tall])) * 0.80
            text = ""
            for c in cal_group:
                if c[3] < 0.5 * H:
                    if c[2] <= c[3] + 2 and c[1] > cal_group[0][1] + 0.4 * H:
                        text += "."
                    else:
                        text += "-"
                    continue
                for ch, sc, mg, _ in read_component(ink, c, "0123456789", pitch):
                    text += ch
                    confs.append(_conf(sc, mg))
                    r.debug += f"{ch}({sc:.2f},{mg:.2f}) "
            text = text.strip("-")
            if "-" in text or not text or text.startswith(".") or text.endswith("."):
                r.notes.append(f"caliber unclear '{text}'")
                confs.append(0.0)
            r.caliber = text or None
    r.debug += f"cal={r.caliber} "

    # dependency letters (after the arrow)
    if dep_group:
        letters = [c for c in dep_group[1:] if c[3] >= 0.5 * H]
        if not letters:
            r.notes.append("arrow without readable letters")
            confs.append(0.0)
        for c in letters:
            pitch = H * 0.80
            for ch, sc, mg, _ in read_component(ink, c, "ABCDEFGH", pitch):
                r.deps.append(ch)
                confs.append(_conf(sc, mg))
                r.debug += f"dep={ch}({sc:.2f},{mg:.2f}) "
                if not _accept(sc, mg):
                    r.notes.append(f"dependency letter unclear (read '{ch}')")
    r.confidence = min(confs) if confs else 100.0
    r.ok = r.badge is not None or bool(comps)
    return r


def layout_recognised(readings: List[CardReading]) -> bool:
    """True when the cards look like the game's layout (most badges read and
    every card has its caliber/arrow row)."""
    if not readings:
        return False
    badges = sum(1 for r in readings if r.badge is not None)
    rows = sum(1 for r in readings
               if r.mounted or "no caliber/arrow row found" not in r.notes)
    return badges >= max(2, (len(readings) + 1) // 2) and rows == len(readings)
