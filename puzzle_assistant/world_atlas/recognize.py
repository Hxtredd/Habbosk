"""Reading the hotel's own interface (read-only): current room name, room ID,
owner, taxi messages and the loading screen.

Agreed with the owner during calibration (DEVELOPMENT_NOTES.md, World Atlas):
  * room name  - the bar above the chat: a yellow pin icon, then the name;
                 always on screen while in a room;
  * room ID    - 'habbosk.us/room/<id>' in the share panel ("¡Más amigos, más
                 diversión!"), which the player keeps open; it may be anywhere
                 (it jumps to the middle after a room change);
  * owner      - "Propietario <name>" in the "Información del lugar" panel;
  * taxi       - chat lines "*Pide un Taxi VIP para ir a <name>*",
                 "*Llegó a su destino*", "[Taxi SK] No pudimos encontrar ese
                 lugar.", "A ese lugar solo se llega caminando...";
  * walking    - black flash, then a plain grey loading screen (~1 s).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..vision import dhash, prep_for_ocr, to_gray

ROOM_URL = re.compile(r"habbosk\.?\s*us\s*/\s*f?r\s*oom\s*/\s*(\d{1,12})", re.I)
TAXI_TO = re.compile(r"Taxi\s+VIP\s+para\s+ir\s+a\s+(.+?)\*?$", re.I)


@dataclass
class RoomView:
    """What the screen says about the current room (None = not visible)."""
    name: Optional[str] = None
    name_box: Optional[Tuple[int, int, int, int]] = None
    room_id: Optional[int] = None
    url_box: Optional[Tuple[int, int, int, int]] = None
    owner: Optional[str] = None
    loading: bool = False
    in_room: bool = False
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Name bar (pin icon + room name, above the chat input)
# ---------------------------------------------------------------------------
def chat_icon_below(img: np.ndarray, box: Tuple[int, int, int, int]
                    ) -> Optional[Tuple[int, int]]:
    """Centre (x, y) of the game's chat icon (pink speech bubble) in the chat
    input right below a room-name bar `box`, or None."""
    x, y, w, h = (int(v) for v in box)
    y0, y1 = y + h, min(img.shape[0], y + h + 5 * h)
    x0 = max(0, x - 2 * h)
    band = img[y0:y1, x0:x + w]
    if band.size == 0:
        return None
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    pink = ((hsv[..., 0] > 140) & (hsv[..., 0] < 178) & (hsv[..., 1] > 70)
            & (hsv[..., 2] > 110)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(pink)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, a = st[i]
        if 0.3 * h <= bw <= 1.2 * h and 0.3 * h <= bh <= 1.2 * h and a >= 0.2 * bw * bh:
            if best is None or bx < best[0]:
                best = (bx, by, bw, bh)
    if best is None:
        return None
    bx, by, bw, bh = best
    return (x0 + bx + bw // 2, y0 + by + bh // 2)


def find_name_bar(img: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """The text area of the room-name bar, or None.

    v3.1: a yellow pin-like shape only counts when the game's chat input (its
    pink chat icon) is right below it - a yellow emoji in Discord or another
    window next to a dark area is not the hotel's name bar (it made SK read the
    wrong monitor: no room recognised, 'Chat box not found')."""
    H, W = img.shape[:2]
    lo = H // 2
    hsv = cv2.cvtColor(img[lo:], cv2.COLOR_BGR2HSV)
    pin = ((hsv[..., 0] > 12) & (hsv[..., 0] < 35) & (hsv[..., 1] > 120)
           & (hsv[..., 2] > 180)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(pin)
    g = to_gray(img).astype(np.int16)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (6 <= w <= 18 and 8 <= h <= 22 and 0.5 <= w / h <= 1.1 and a >= 0.35 * w * h):
            continue
        y += lo
        x0, x1 = x + w + 6, min(W, x + w + 6 + int(0.36 * W))
        y0, y1 = max(0, y - 4), min(H, y + h + 4)
        strip = g[y0:y1, x0:x1]
        left = g[y0:y1, max(0, x - 14):x - 2]
        if strip.size == 0 or left.size == 0:
            continue
        # a calm dark panel: mostly dark pixels around the text
        if np.mean(strip < 60) < 0.75 or np.median(left) > 60:
            continue
        # the chat input sits below it: another dark band
        below = g[min(H - 1, y + h + 14):min(H, y + h + 50), x0:x1]
        if below.size and np.median(below) > 70:
            continue
        cand = (x0, y0, x1 - x0, y1 - y0)
        if chat_icon_below(img, cand) is None:
            continue                       # no chat input under it: not the name bar
        if best is None or y > best[1]:
            best = cand
    return best


def read_name(img: np.ndarray, ocr, cache: Optional[dict] = None
              ) -> Tuple[Optional[str], Optional[tuple]]:
    box = find_name_bar(img)
    if box is None:
        return None, None
    x, y, w, h = box
    crop = img[y:y + h, x:x + w]
    key = ("name", dhash(crop, 16))
    if cache is not None and key in cache:
        return cache[key], box                # same picture: no new text reading
    g = to_gray(crop)
    cols = np.where((g > 140).any(axis=0))[0]
    if len(cols) == 0:
        return None, box
    # the text ends at the first wide gap (then come the (i) / history icons)
    gaps = np.where(np.diff(cols) > max(30, 3 * h))[0]
    end = cols[gaps[0]] if len(gaps) else cols[-1]
    crop = crop[:, :end + 4]
    text = ocr.text(prep_for_ocr(crop, scale=3.0), psm=7).strip()
    text = re.sub(r"^[^\wÁÉÍÓÚÑáéíóúñ¿¡]+", "", text).strip(" |·.")
    if cache is not None:
        _remember(cache, key, text or None)
    return (text or None), box


def _remember(cache: dict, key, value) -> None:
    if len(cache) > 400:
        cache.clear()
    cache[key] = value


# ---------------------------------------------------------------------------
# Share panel URL -> room ID
# ---------------------------------------------------------------------------
def _url_boxes(img: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """Input-box-like rectangles (the share panel's URL field)."""
    g = to_gray(img)
    H, W = g.shape
    e = cv2.Canny(g, 30, 90)
    e = cv2.dilate(e, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if 0.10 * W <= w <= 0.20 * W and 0.018 * H <= h <= 0.04 * H and w / h > 6:
            out.append((x, y, w, h))
    out.sort(key=lambda b: (b[1], b[0]))
    keep = []
    for b in out:
        if not any(abs(b[0] - k[0]) < 8 and abs(b[1] - k[1]) < 8 for k in keep):
            keep.append(b)
    return keep


def read_room_id(img: np.ndarray, ocr, cache: Optional[dict] = None,
                 where: Optional[tuple] = None) -> Tuple[Optional[int], Optional[tuple]]:
    """The number after habbosk.us/room/ in the share panel, if visible.
    where = the box found last time: checked first (no full-screen search)."""
    boxes = [where] if where is not None else _url_boxes(img)
    for (x, y, w, h) in boxes:
        crop = img[y + 2:y + h - 2, x + 3:x + w - 3]
        if crop.size == 0:
            continue
        g = to_gray(crop)
        if not (g > 150).any():
            continue
        key = ("url", dhash(crop, 16))
        if cache is not None and key in cache:
            rid = cache[key]
        else:
            rid = _read_url(crop, ocr)
            if cache is not None:
                _remember(cache, key, rid)
        if rid is not None:
            return rid, (x, y, w, h)
    return None, None


def _read_url(crop: np.ndarray, ocr) -> Optional[int]:
    """Read the URL twice at different zoom levels; the room ID counts only
    when both readings agree (a wrong digit must never become a room ID)."""
    ids = []
    for scale in (3.0, 2.2):
        m = ROOM_URL.search(ocr.text(prep_for_ocr(crop, scale=scale), psm=7).replace(" ", ""))
        if not m:
            return None
        ids.append(m.group(1))
    return int(ids[0]) if ids[0] == ids[1] else None


# ---------------------------------------------------------------------------
# Owner ("Propietario X" in the info panel)
# ---------------------------------------------------------------------------
_OWNER = re.compile(r"Propietario\s+\W*\s*([A-Za-z0-9_.\-]+)")


def read_owner(img: np.ndarray, ocr, near: Optional[tuple]) -> Optional[str]:
    """Look for 'Propietario <name>' in the panels near the share panel."""
    if near is None:
        return None
    H, W = img.shape[:2]
    x, y, w, h = near
    x0, x1 = max(0, x - w // 2), min(W, x + int(w * 1.6))
    y0, y1 = max(0, y - int(6 * h)), min(H, y + int(12 * h))
    words = ocr.words(prep_for_ocr(img[y0:y1, x0:x1], scale=2.0), psm=11)
    toks = [wd.text for wd in words]
    for i, t in enumerate(toks):
        if t.lower().startswith("propietario"):
            for nxt in toks[i + 1:i + 3]:
                nxt = re.sub(r"^[^\w]+", "", nxt)
                if len(nxt) >= 2:
                    return nxt
    return None


# ---------------------------------------------------------------------------
# Loading screen (walking through an arrow)
# ---------------------------------------------------------------------------
def thumb(img: np.ndarray) -> np.ndarray:
    """Small grey picture of the room area, for cheap motion checks."""
    g = to_gray(img)
    H, W = g.shape
    mid = g[int(H * 0.15):int(H * 0.8), int(W * 0.3):int(W * 0.95)]
    return cv2.resize(mid, (96, 54), interpolation=cv2.INTER_AREA)


def flatness(t: np.ndarray) -> float:
    """Share of the room area that is one flat colour (loading screens ~0.75+)."""
    return float(np.mean(np.abs(t.astype(np.int16) - int(np.median(t))) < 4))


def read_view(img: np.ndarray, ocr, cache: Optional[dict] = None,
              want_owner: bool = False, url_where: Optional[tuple] = None,
              search_url: bool = True) -> RoomView:
    """url_where: where the share panel's URL was last time (checked first);
    search_url=False: don't search the whole screen for it this time."""
    v = RoomView()
    v.name, v.name_box = read_name(img, ocr, cache)
    v.in_room = v.name_box is not None
    v.room_id, v.url_box = None, None
    if url_where is not None:
        v.room_id, v.url_box = read_room_id(img, ocr, cache, url_where)
    if v.room_id is None and search_url:
        v.room_id, v.url_box = read_room_id(img, ocr, cache)
    if want_owner and v.url_box is not None:
        v.owner = read_owner(img, ocr, v.url_box)
    return v


def taxi_destination(lines: List[str]) -> Optional[str]:
    for ln in lines:
        m = TAXI_TO.search(ln)
        if m:
            return m.group(1).strip(" *")
    return None
