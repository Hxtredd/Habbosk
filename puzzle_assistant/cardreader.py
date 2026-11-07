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

from .glyphs import Box, classify, components, crop, split_touching

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
    debug: str = ""


def _ink(card: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Brightness above the card background (colour-independent) and a mask."""
    mx = card.max(axis=2).astype(np.int16) if card.ndim == 3 else card.astype(np.int16)
    bg = int(np.median(mx))
    hi = int(np.percentile(mx, 99.5))
    ink = np.clip(mx - bg, 0, 255).astype(np.uint8)
    thr = max(40, int(0.40 * (hi - bg)))
    return ink, ink > thr


def _merge_close(boxes: List[Box], gap: float) -> List[Box]:
    out: List[Box] = []
    for b in sorted(boxes, key=lambda b: b[0]):
        if out and b[0] - (out[-1][0] + out[-1][2]) <= gap:
            x0, y0, w0, h0 = out[-1]
            x1, y1 = max(x0 + w0, b[0] + b[2]), max(y0 + h0, b[1] + b[3])
            nx, ny = min(x0, b[0]), min(y0, b[1])
            out[-1] = (nx, ny, x1 - nx, y1 - ny)
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
    b = max(2, int(round(min(h, w) * 0.06)))       # ignore the card's outline
    mask[:b, :] = mask[-b:, :] = False
    mask[:, :b] = mask[:, -b:] = False
    confs: List[float] = []

    # ---------------- badge letter (left part) -------------------------------
    left = mask.copy()
    left[:, int(w * 0.30):] = False
    comps = [c for c in components(left) if c[3] >= h * 0.08]
    if comps:
        bx = max(comps, key=lambda c: c[3])
        ch, sc, mg = classify(crop(ink, bx), "ABCDEFGH")
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
    bot = mask.copy()
    bot[:int(h * 0.50), :] = False
    bot[:, :int(w * 0.25)] = False
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
    merged = _merge_close(groups[-1], max(1.0, 0.3 * H))
    if is_arrow(merged[0]) and merged[0][0] > w * 0.45:
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
                pieces = split_touching(c, ink, pitch) if c[2] > 1.25 * pitch else [c]
                for p in pieces:
                    ch, sc, mg = classify(crop(ink, p), "0123456789")
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
            for p in (split_touching(c, ink, pitch) if c[2] > 1.4 * pitch else [c]):
                ch, sc, mg = classify(crop(ink, p), "ABCDEFGH")
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
    rows = sum(1 for r in readings if "no caliber/arrow row found" not in r.notes)
    return badges >= max(2, (len(readings) + 1) // 2) and rows == len(readings)
