"""Image-processing helpers: preprocessing, panel/card segmentation, hashing."""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]  # x, y, w, h


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def prep_for_ocr(img: np.ndarray, scale: float = 2.0, pad: int = 12) -> np.ndarray:
    """Grayscale, dark-on-light, upscaled, padded: what Tesseract likes best."""
    g = to_gray(img)
    # Contrast-stretch: makes coloured text on coloured cards readable.
    lo, hi = np.percentile(g, (0.5, 99.5))
    if hi - lo > 10:
        g = np.clip((g.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    # Background = the dominant tone (most pixels). Text must end up dark.
    # (The image border is not used: card/panel outlines would fool it.)
    if np.median(g) < 128:
        g = 255 - g
    if scale and scale != 1.0:
        interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=interp)
    if pad:
        bg = int(np.median(g))
        g = cv2.copyMakeBorder(g, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=bg)
    return g


def dhash(img: np.ndarray, size: int = 8) -> int:
    g = to_gray(img)
    small = cv2.resize(g, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    v = 0
    for bit in diff.flatten():
        v = (v << 1) | int(bit)
    return v


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _iou(a: Rect, b: Rect) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


def _contains(outer: Rect, inner: Rect, slack: int = 2) -> bool:
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack and
            inner[0] + inner[2] <= outer[0] + outer[2] + slack and
            inner[1] + inner[3] <= outer[1] + outer[3] + slack)


def rect_candidates(gray: np.ndarray, min_area: float, max_area: float) -> List[Rect]:
    """Rectangular-ish regions (boxes, cards, panels) found from edges."""
    edges = cv2.Canny(gray, 30, 100)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rects: List[Rect] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < min_area or area > max_area or w < 12 or h < 12:
            continue
        hull_area = cv2.contourArea(cv2.convexHull(c))
        if hull_area < 0.80 * area:  # not a rectangle
            continue
        rects.append((x, y, w, h))
    # de-duplicate (inner/outer edge of the same border)
    rects.sort(key=lambda r: -r[2] * r[3])
    out: List[Rect] = []
    for r in rects:
        if all(_iou(r, o) < 0.80 for o in out):
            out.append(r)
    return out


def reading_order(rects: List[Rect]) -> List[Rect]:
    if not rects:
        return []
    med_h = float(np.median([r[3] for r in rects]))
    rows: List[List[Rect]] = []
    for r in sorted(rects, key=lambda r: r[1] + r[3] / 2):
        cy = r[1] + r[3] / 2
        if rows and abs(cy - (rows[-1][0][1] + rows[-1][0][3] / 2)) < med_h * 0.5:
            rows[-1].append(r)
        else:
            rows.append([r])
    return [r for row in rows for r in sorted(row, key=lambda r: r[0])]


MIN_PIECES, MAX_PIECES = 3, 8


def find_cards(panel: np.ndarray, expected: int = 0) -> Optional[List[Rect]]:
    """Find the piece boxes: the biggest set of same-sized, non-overlapping
    boxes in the panel. The number of pieces varies between puzzles (7, 8...),
    so any count from MIN_PIECES to MAX_PIECES is accepted unless `expected`
    (> 0) forces one."""
    gray = to_gray(panel)
    H, W = gray.shape[:2]
    area = H * W
    lo_n = expected or MIN_PIECES
    rects = rect_candidates(gray, area / 120.0, area / (lo_n * 0.9))
    best: Optional[List[Rect]] = None
    best_key = (-1, -1.0)
    for r in rects:
        group = [o for o in rects
                 if abs(o[2] - r[2]) <= 0.12 * r[2] and abs(o[3] - r[3]) <= 0.12 * r[3]]
        # drop boxes nested inside other boxes of the group
        group = [g for g in group if not any(o is not g and _contains(o, g) for o in group)]
        n = len(group)
        if (expected and n != expected) or not (MIN_PIECES <= n <= MAX_PIECES):
            continue
        key = (n, float(np.median([g[2] * g[3] for g in group])))
        if key > best_key:
            best, best_key = group, key
    if best is None:
        return None
    return reading_order(best)


def mask_rects(img: np.ndarray, rects: List[Rect]) -> np.ndarray:
    """Paint rects with the background colour of the area OUTSIDE them
    (hides the cards so the header can be read on its own)."""
    keep = np.ones(img.shape[:2], dtype=bool)
    for x, y, w, h in rects:
        keep[max(0, y - 3):y + h + 3, max(0, x - 3):x + w + 3] = False
    out = img.copy()
    pix = img[keep] if keep.any() else img.reshape(-1, *img.shape[2:])
    fill = np.median(pix, axis=0)
    out[~keep] = fill.astype(img.dtype)
    return out


def find_big_glyph(prepped: np.ndarray) -> Optional[Rect]:
    """Largest stand-alone character on a card (usually the card's letter).

    `prepped` is the output of prep_for_ocr (dark text on light background).
    """
    _, bw = cv2.threshold(prepped, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    comps = [tuple(stats[i][:4]) for i in range(1, n)
             if stats[i][3] >= 6 and stats[i][4] >= 10]
    H, W = prepped.shape[:2]
    # ignore frame lines / borders
    comps = [c for c in comps if c[2] < W * 0.6 and c[3] < H * 0.6]
    if len(comps) < 2:
        return None
    med_h = float(np.median([c[3] for c in comps]))
    cands = [c for c in comps if c[3] >= 1.35 * med_h and 0.25 * c[3] <= c[2] <= 1.3 * c[3]]
    if not cands:
        return None
    x, y, w, h = max(cands, key=lambda c: (c[3], -c[1], -c[0]))
    return (int(x), int(y), int(w), int(h))


def find_grids(gray: np.ndarray, min_n: int = 5, max_n: int = MAX_PIECES) -> List[List[Rect]]:
    """Whole-frame search for grids of same-sized boxes (puzzle piece cards).

    No OCR and no title needed, so it is independent of the (translucent)
    puzzle background and of other places showing the same title text.
    Returns candidate grids (reading order), biggest boxes first.
    """
    H, W = gray.shape[:2]
    A = H * W
    rects = rect_candidates(gray, A / 4000.0, A / (min_n * 1.2))
    rects = [r for r in rects if 1.2 <= r[2] / max(1, r[3]) <= 4.5]  # card-shaped
    used = set()
    grids: List[List[Rect]] = []
    for i, r in enumerate(rects):
        if i in used:
            continue
        same = [j for j, o in enumerate(rects)
                if abs(o[2] - r[2]) <= 0.08 * r[2] and abs(o[3] - r[3]) <= 0.08 * r[3]]
        # keep only boxes connected to r through small gaps (one grid)
        cluster = {i}
        frontier = [i]
        while frontier:
            a = rects[frontier.pop()]
            for j in same:
                if j in cluster:
                    continue
                b = rects[j]
                gx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]))
                gy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]))
                if gx <= 0.5 * r[3] and gy <= 0.5 * r[3]:
                    cluster.add(j)
                    frontier.append(j)
        used |= cluster
        boxes = [rects[j] for j in cluster]
        boxes = [g for g in boxes if not any(o is not g and _contains(o, g) for o in boxes)]
        if min_n <= len(boxes) <= max_n:
            grids.append(reading_order(boxes))
    grids.sort(key=lambda g: -g[0][2] * g[0][3])
    return grids


def panel_around(cards: List[Rect], frame_shape) -> Rect:
    """Area that holds the cards plus the header line above them
    ('Fusil · calibre 5.56') and the footer below ('Montadas 0/8')."""
    H, W = frame_shape[:2]
    ch = int(np.median([c[3] for c in cards]))
    cw = int(np.median([c[2] for c in cards]))
    x0 = max(0, min(c[0] for c in cards) - int(0.15 * cw))
    x1 = min(W, max(c[0] + c[2] for c in cards) + int(0.15 * cw))
    y0 = max(0, min(c[1] for c in cards) - int(1.6 * ch))
    y1 = min(H, max(c[1] + c[3] for c in cards) + int(1.0 * ch))
    return (x0, y0, x1 - x0, y1 - y0)
