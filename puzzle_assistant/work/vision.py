"""Reading the job screen for the work helper (pure functions, testable on
pictures; nothing here touches the mouse or keyboard).

Learned from the owner's recordings (Armería, 1920x1080):
  * the job panel (top left) lists the offered tasks ("Montar un arma /
    a 6 casillas / Recomendado ... [Ir]"), and while a task runs its step
    ("Ve al objetivo  Paso 1/3", "Llevas 1/1 cajas", "Lleva la carga a su
    sitio  Paso 2/3", "Trabajando...") with the task marked "En curso";
  * things to pick up carry a teal "!" bubble with a label below it
    ("Munición", "Basura"; the desks' "Tarea" bubbles are other tasks);
  * where to take what you carry is shown by a teal down-arrow with a label
    and a ring on the floor ("Armero", "Contenedor");
  * clicking an object opens a small window with a yellow button ("Coger",
    "Recoger") - pressing it walks the character there and does it;
  * energy is the lime bar in the top bar, with its number on the right.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

Box = Tuple[int, int, int, int]          # x, y, w, h (screen-picture pixels)
Point = Tuple[int, int]

# Labels seen in the job room. Unknown labels are never clicked.
LABELS = ("Tarea", "Munición", "Basura", "Contenedor", "Armero")
# Tasks the helper does (walk + carry), with the label of the thing to pick up.
WALKING_TASKS = {"Colocar la munición": "Munición", "Barrer casquillos": "Basura"}
# Tasks it leaves to the owner (mini-games not automated yet).
MANUAL_TASKS = ("Montar un arma", "Limpiar el banco")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------------------
# Teal markers
# ---------------------------------------------------------------------------
def teal_mask(img: np.ndarray) -> np.ndarray:
    """The markers' teal, by colour tone rather than brightness: in the game's
    night the room is darker (owner's screenshot 2026-11-12: ~RGB 32,128,112
    instead of 40,184,176 by day)."""
    b, g, r = (img[..., i].astype(np.int16) for i in range(3))
    return ((g > 95) & (b > 0.72 * g) & (b < 1.12 * g) & (r < 0.45 * g)
            & (g - r > 70)).astype(np.uint8)


@dataclass
class Marker:
    kind: str                     # "bubble" (something to pick up / a task) or "arrow" (where to go)
    box: Box                      # the teal shape
    label_box: Optional[Box] = None
    label: Optional[str] = None   # one of LABELS, None = not read / unknown
    raw: str = ""                 # what OCR read

    @property
    def click_point(self) -> Point:
        """Where to click. Bubble: the object just below its label (clicking it
        opens the object's window). Arrow: its label / floor ring."""
        if self.label_box is not None:
            x, y, w, h = self.label_box
            if self.kind == "bubble":
                return x + w // 2, y + h + max(8, h)
            return x + w // 2, y + h // 2 + 2
        x, y, w, h = self.box
        return x + w // 2, y + h + 20


def find_markers(img: np.ndarray, scale: float = 1.0) -> List[Marker]:
    """Teal '!' bubbles and down-arrows (sizes from the recordings: bubble
    22x20 px with ~300 teal px, arrow 16-18x22 px with ~190)."""
    n, _, st, _ = cv2.connectedComponentsWithStats(teal_mask(img), 8)
    out: List[Marker] = []
    s2 = scale * scale
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if y < img.shape[0] * 0.12:              # the browser / top bar icons
            continue
        # a bubble whose top is hidden (something in front of it) still counts
        if 16 * scale <= w <= 28 * scale and 12 * scale <= h <= 26 * scale \
                and 150 * s2 <= a <= 380 * s2 and w >= h * 0.95:
            out.append(Marker("bubble", (x, y, w, h)))
        elif 12 * scale <= w <= 22 * scale and 18 * scale <= h <= 27 * scale \
                and 140 * s2 <= a <= 240 * s2 and h > w:
            out.append(Marker("arrow", (x, y, w, h)))
    return out


def _run_at(row: np.ndarray, c: int) -> Optional[Tuple[int, int]]:
    if c < 0 or c >= len(row) or not row[c]:
        return None
    a = c
    while a > 0 and row[a - 1]:
        a -= 1
    b = c
    while b < len(row) - 1 and row[b + 1]:
        b += 1
    return a, b


def label_pixels(img: np.ndarray, m: Marker) -> Tuple[Optional[Box], Optional[np.ndarray]]:
    """The dark label pill under a marker and its white text (as a 0/255 mask).
    The pill's top and bottom edges are long dark rows; the text between them
    is near-white."""
    x, y, w, h = m.box
    cx = x + w // 2
    x0, y0 = max(0, cx - 90), max(0, y + h - 2)
    c = img[y0:y0 + 40, x0:cx + 90].astype(np.int16)
    if c.size == 0:
        return None, None
    dark = c.max(axis=2) < 85
    cc = cx - x0
    rows = []
    for r in range(dark.shape[0]):
        best = None
        for dc in range(-6, 7):
            ru = _run_at(dark[r], cc + dc)
            if ru and ru[1] - ru[0] >= 24 and (best is None or ru[1] - ru[0] > best[1] - best[0]):
                best = ru
        rows.append(best)
    # first the daytime look (white text) over every possible pill; only if
    # none has it, the night look (dimmer text, judged against the pill)
    for night in (False, True):
        for top in range(len(rows)):
            if rows[top] is None:
                continue
            for bot in range(min(top + 20, len(rows) - 1), top + 8, -1):
                if rows[bot] is None:
                    continue
                a, b = max(rows[top][0], rows[bot][0]), min(rows[top][1], rows[bot][1])
                if b - a < 24:
                    continue
                t = c[top + 1:bot, a + 1:b]
                grey = (t.max(axis=2) - t.min(axis=2)) < 50
                if not night:
                    txt = ((t.min(axis=2) > 185) & grey).astype(np.uint8) * 255
                else:
                    lum = t.min(axis=2)
                    pill = float(np.median(c[top, a:b].max(axis=1)))
                    hi = float(np.percentile(lum, 97))
                    if hi - pill < 70:
                        continue
                    thr = max(110.0, hi - 0.3 * (hi - pill))
                    txt = ((lum > thr) & grey).astype(np.uint8) * 255
                if int(txt.sum()) // 255 >= 15:
                    return (x0 + a, y0 + top, b - a, bot - top), txt
    return None, None


def match_label(text: str, choices: Sequence[str] = LABELS) -> Optional[str]:
    """The known label OCR text most likely is - only when clearly one of them."""
    t = re.sub(r"[^a-z]", "", norm(text))
    if len(t) < 3:
        return None
    scored = sorted(((difflib.SequenceMatcher(None, t, norm(c)).ratio(), c) for c in choices), reverse=True)
    best, second = scored[0], scored[1] if len(scored) > 1 else (0.0, "")
    if best[0] >= 0.6 and best[0] - second[0] >= 0.15:
        return best[1]
    return None


def ocr_ready(txt_mask: np.ndarray) -> np.ndarray:
    """Black text on white, 4x, with a margin (what Tesseract reads best)."""
    t = 255 - cv2.resize(txt_mask, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    return cv2.copyMakeBorder(t, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def label_patch(img: np.ndarray, m: Marker) -> Tuple[Optional[Box], Optional[np.ndarray]]:
    """Second try (owner, 2026-11-12: a label half hidden behind the floor
    ring, trash and other markers): the light grey text in a fixed patch
    right under the marker, judged against the patch's own darkness."""
    x, y, w, h = m.box
    cx = x + w // 2
    x0, y0 = max(0, cx - 38), max(0, y + h)
    c = img[y0:y0 + 26, x0:cx + 38].astype(np.int16)
    if c.size == 0:
        return None, None
    lum = c.min(axis=2)
    p50, p97 = np.percentile(lum, [50, 97])
    if p97 - p50 < 60:
        return None, None
    thr = max(110.0, p97 - 0.15 * (p97 - p50))
    txt = ((lum > thr) & ((c.max(axis=2) - c.min(axis=2)) < 40)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(txt, 8)
    keep = [i for i in range(1, n) if st[i][3] >= 5 and st[i][4] >= 4]
    if not keep:
        return None, None
    txt = np.isin(lab, keep).astype(np.uint8) * 255
    # the word: the widest row of letters (a stray blob on the side is left out)
    xs = sorted((int(st[i][0]), int(st[i][0] + st[i][2])) for i in keep)
    runs, cur = [], [xs[0][0], xs[0][1]]
    for a, b in xs[1:]:
        if a - cur[1] <= 6:
            cur[1] = max(cur[1], b)
        else:
            runs.append(cur)
            cur = [a, b]
    runs.append(cur)
    a, b = max(runs, key=lambda r: r[1] - r[0])
    txt[:, :max(0, a - 1)] = 0
    txt[:, b + 1:] = 0
    ys = np.nonzero(txt.any(axis=1))[0]
    box = (x0 + a - 3, y0 + int(ys[0]) - 3, b - a + 6, int(ys[-1] - ys[0]) + 7)
    return box, txt


def read_labels(img: np.ndarray, markers: List[Marker], ocr_line: Callable[[np.ndarray], str]) -> None:
    for m in markers:
        box, txt = label_pixels(img, m)
        m.label_box = box
        if txt is not None:
            m.raw = ocr_line(ocr_ready(txt)).strip()
            m.label = match_label(m.raw)
        if m.label is None:
            box2, txt2 = label_patch(img, m)
            if txt2 is not None:
                raw = ocr_line(ocr_ready(txt2)).strip()
                lab = match_label(raw)
                if lab is not None:
                    m.label, m.raw, m.label_box = lab, raw, box2


# ---------------------------------------------------------------------------
# Yellow action buttons ("Coger", "Recoger")
# ---------------------------------------------------------------------------
def yellow_buttons(img: np.ndarray) -> List[Box]:
    b, g, r = (img[..., i].astype(np.int16) for i in range(3))
    m = ((r > 220) & (g > 170) & (g < 225) & (b < 110)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if 22 <= h <= 48 and 90 <= w <= 320 and a >= 0.6 * w * h:
            out.append((x, y, w, h))
    return out


def new_button_near(before: List[Box], after: List[Box], point: Point, within: int = 320) -> Optional[Box]:
    """The yellow button that appeared after clicking `point` (not there before,
    near the click). None when there is no such button or more than one."""
    def same(a, b):
        return abs(a[0] - b[0]) <= 6 and abs(a[1] - b[1]) <= 6 and abs(a[2] - b[2]) <= 10
    fresh = [a for a in after if not any(same(a, b) for b in before)]
    near = [a for a in fresh
            if abs(a[0] + a[2] / 2 - point[0]) <= within and abs(a[1] + a[3] / 2 - point[1]) <= within]
    return near[0] if len(near) == 1 else None


def button_click_point(box: Box) -> Point:
    """The button's text part (its right end holds small extra icons)."""
    x, y, w, h = box
    return x + int(w * 0.3), y + h // 2


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------
TRACK_BGR = (58, 50, 44)


@dataclass
class Energy:
    fraction: float              # how full the bar is (0..1)
    value: Optional[int]         # the number next to it (None = not read)
    bar: Box                     # the whole bar (track)
    number_box: Box


def find_energy_bar(img: np.ndarray, top_part: float = 1 / 3) -> Optional[Tuple[Box, int]]:
    """(whole bar box, filled width) of the lime energy bar in the top part."""
    top = img[:max(1, int(img.shape[0] * top_part))].astype(np.int16)
    b, g, r = top[..., 0], top[..., 1], top[..., 2]
    lime = ((g > 200) & (r > 150) & (r < 235) & (b < 130)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(lime, 8)
    bars = [tuple(int(v) for v in s) for s in st[1:] if 4 <= s[3] <= 14 and s[2] >= 2 * s[3]]
    if not bars:
        return None
    x, y, w, h, _ = max(bars, key=lambda s: s[2])
    row = top[y + h // 2]
    # where the number starts (first bright pixel after the fill)
    num = x + w + 2
    while num < row.shape[0] and int(row[num].max()) < 150:
        num += 1
    if num >= row.shape[0]:
        return None
    # the bar's dark track is clearly lighter than the background just before
    # the number (day: 58,50,44 on 35,24,19; night: 40,35,36 on 19,12,13)
    bg = int(row[max(x + w, num - 2)].sum())
    end = x + w + 3                                   # past the fill's soft edge
    while end < num and int(row[end].sum()) > bg + 30:
        end += 1
    track = max(end - x, w)
    return (x, y, track, h), w


def read_energy(img: np.ndarray, ocr_digits: Optional[Callable[[np.ndarray], str]] = None,
                top_part: float = 1 / 3) -> Optional[Energy]:
    found = find_energy_bar(img, top_part)
    if found is None:
        return None
    (x, y, tw, h), w = found
    nb = (x + tw + 4, max(0, y - 8), 50, h + 16)
    value = None
    if ocr_digits is not None:
        crop = img[nb[1]:nb[1] + nb[3], nb[0]:nb[0] + nb[2]]
        if crop.size:
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            _, t = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            t = cv2.copyMakeBorder(t, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
            digits = re.sub(r"\D", "", ocr_digits(t) or "")
            if digits and len(digits) <= 3:
                value = int(digits)
    return Energy(w / max(1, tw), value, (x, y, tw, h), nb)


class EnergyJudge:
    """Decides 'energy is low' only when the number and the bar agree.

    The bar's full length = the maximum energy (about 150 in the recordings);
    the number is checked against the bar so a misread number (e.g. "19" for
    119) is not believed."""

    def __init__(self, threshold: int):
        self.threshold = threshold
        self.max_est: Optional[float] = None

    def judge(self, e: Optional[Energy]) -> Tuple[Optional[bool], Optional[int]]:
        """(low?, energy) - low None = can't tell."""
        if e is None or e.fraction <= 0:
            return None, None
        if e.value is not None and e.fraction > 0.08:
            est = e.value / e.fraction
            if self.max_est is None and 60 <= est <= 400:
                self.max_est = est
            elif self.max_est is not None and abs(est - self.max_est) <= 0.12 * self.max_est:
                self.max_est = 0.8 * self.max_est + 0.2 * est
            if self.max_est is not None and abs(est - self.max_est) <= 0.12 * self.max_est:
                return e.value < self.threshold, e.value
        if self.max_est is not None:                  # the number misread: use the bar
            v = int(round(e.fraction * self.max_est))
            return v < self.threshold, v
        return None, e.value


# ---------------------------------------------------------------------------
# Job panel
# ---------------------------------------------------------------------------
@dataclass
class Offer:
    title: str                    # as read
    task: Optional[str]           # known task name (WALKING_TASKS / MANUAL_TASKS) or None
    recommended: bool
    ir: Optional[Point]           # where its "Ir" button is (picture px)


@dataclass
class PanelState:
    found: bool
    offers: List[Offer] = field(default_factory=list)
    active: Optional[str] = None          # task marked "En curso"
    step: Optional[Tuple[int, int]] = None
    going: bool = False                   # "Ve al objetivo"
    carrying: bool = False                # "Llevas 1/1 ..."
    load: Optional[Tuple[int, int]] = None   # "Llevas 1/3 de basura" -> (1, 3)
    load_kind: Optional[str] = None       # the label of what is carried ("Basura", "Munición")
    deliver: bool = False                 # "Lleva la carga a su sitio"
    working: bool = False                 # "Trabajando..." (a mini-game / action at the spot)
    remaining: Optional[int] = None       # "Te quedan N tareas pagadas"
    text: str = ""

    @property
    def busy(self) -> bool:
        return bool(self.active or self.going or self.carrying or self.deliver or self.working)


def task_name(title: str) -> Optional[str]:
    t = norm(title)
    if "munici" in t or "colocar" in t:
        return "Colocar la munición"
    if "casquill" in t or "barrer" in t:
        return "Barrer casquillos"
    if "montar" in t:
        return "Montar un arma"
    if "banco" in t or "limpiar" in t:
        return "Limpiar el banco"
    return None


def lines_of(words, join_px: float = 7.0) -> List[Tuple[float, list]]:
    """Group OCR words (with .cx/.cy/.text) into text lines by height on screen."""
    ws = sorted(words, key=lambda w: w.cy)
    out: List[Tuple[float, list]] = []
    for w in ws:
        if out and abs(out[-1][0] - w.cy) <= join_px:
            y, row = out[-1]
            row.append(w)
            out[-1] = ((y * (len(row) - 1) + w.cy) / len(row), row)
        else:
            out.append((w.cy, [w]))
    return [(y, sorted(row, key=lambda w: w.cx)) for y, row in out]


_IR = re.compile(r"^[\(\[]?[il1|][rn][\)\]]?$", re.I)


def ir_buttons(panel_img: np.ndarray) -> List[Point]:
    """Centres of the "Ir" buttons, found by their white text in the panel's
    right-hand column (OCR misses "Ir" on highlighted cards)."""
    h, w = panel_img.shape[:2]
    x0, x1 = int(w * 0.78), int(w * 0.99)
    c = panel_img[:, x0:x1].astype(np.int16)
    # light text: white, pale teal or teal (the owner's screen: "I" ~ (90, 185, 160))
    white = (c.max(axis=2) > 140).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(white, 8)
    blobs = [tuple(int(v) for v in st[i]) for i in range(1, n)]
    out: List[Point] = []
    # The "I" is a thin upright stroke; right of it only the small "r" (whole or
    # broken into specks). A letter of a word ("l" in "salida") has letters on
    # its left too, or more than one letter-sized neighbour on its right.
    for cand in blobs:
        bx, by, bw, bh, ba = cand
        if bw > 2 or not 7 <= bh <= 12:
            continue
        near = [o for o in blobs if o != cand and o[4] >= 6
                and o[1] < by + bh and o[1] + o[3] > by and abs(o[0] - bx) <= 14]
        left = [o for o in near if o[0] < bx]
        right = [o for o in near if o[0] > bx]
        r_like = all(o[0] - (bx + bw) <= 4 and o[2] <= 6 and o[3] <= bh for o in right)
        if not left and len(right) <= 1 and r_like:
            out.append((x0 + bx + 3, by + bh // 2))
    return out


def parse_panel(words, width: float, panel_img: Optional[np.ndarray] = None) -> PanelState:
    """Words of the panel (coordinates relative to the panel picture, panel
    `width` px wide) -> what it says. With the panel picture the "Ir" buttons
    are also found by their look."""
    lines = lines_of(words)
    texts = [(y, " ".join(w.text for w in row), row) for y, row in lines]
    alltext = norm(" ".join(t for _, t, _ in texts))
    if "jornada" not in alltext and "fichar" not in alltext and "pagadas" not in alltext:
        return PanelState(False, text=alltext)
    p = PanelState(True, text=alltext)
    m = re.search(r"quedan\s+(\d+)\s+tareas", alltext)
    if m:
        p.remaining = int(m.group(1))
    m = re.search(r"paso\s+(\d)\s*/\s*(\d)", alltext)
    if m:
        p.step = (int(m.group(1)), int(m.group(2)))
    p.going = "objetivo" in alltext
    p.carrying = bool(re.search(r"llevas\s+\d", alltext))
    # the "Llevas N/M ..." line (OCR: "ltevas 1/1 cajas", "1/1 llevas de basura")
    for _, t, _ in texts:
        nt = norm(t)
        if re.search(r"[a-z]{1,2}evas\b", nt) and "paso" not in nt:
            m = re.search(r"(\d+)\s*/\s*(\d+)", nt)
            if m and 0 < int(m.group(2)) <= 20 and int(m.group(1)) <= int(m.group(2)):
                p.load = (int(m.group(1)), int(m.group(2)))
                p.carrying = True
                p.load_kind = "Basura" if "basura" in nt else ("Munición" if "caja" in nt else None)
            break
    p.deliver = "a su sitio" in alltext or "lleva la carga" in alltext
    p.working = "trabajando" in alltext
    for i, (y, t, row) in enumerate(texts):
        if re.search(r"en\s*curso", norm(t)):
            for j in (i, i - 1):
                if j >= 0:
                    name = task_name(" ".join(w.text for w in texts[j][2] if w.cx < width * 0.75))
                    if name:
                        p.active = name
                        break
            if p.active:
                break
    irs = [(w.cx, w.cy) for _, _, row in texts for w in row
           if _IR.match(w.text) and w.cx > width * 0.7]
    if panel_img is not None:
        for gx, gy in ir_buttons(panel_img):
            if not any(abs(gy - iy) <= 8 for _, iy in irs):
                irs.append((gx, gy))
    # a task card: its title is the line just above "a N casilla(s)"
    for i, (y, t, row) in enumerate(texts):
        nt = norm(t)
        if "casilla" not in nt or i == 0:
            continue
        ty, title, trow = texts[i - 1]
        left_title = " ".join(w.text for w in trow if w.cx < width * 0.75)
        name = task_name(left_title)
        in_progress = bool(re.search(r"en\s*curso", norm(title) + " " + nt))
        if in_progress and p.active:
            continue
        if in_progress:
            p.active = name or left_title
            continue
        ir = None
        cands = [(x, iy) for x, iy in irs if ty - 10 <= iy <= y + 22]
        if cands:
            ir = min(cands, key=lambda c: abs(c[1] - (ty + y) / 2))
        recommended = bool(re.search(r"recom|com[ae]nd|m[ae]nd[ae]do|re\w* m\w* dado", nt))
        p.offers.append(Offer(left_title, name, recommended, (int(ir[0]), int(ir[1])) if ir else None))
    return p


def find_panel(words, origin: Point = (0, 0), scale: float = 1.0) -> Optional[Box]:
    """Where the job panel is, from OCR words of a bigger area (coordinates of
    those words / scale + origin = screen-picture px). Anchored on the panel's
    "Fichar salida" button (top right corner of the panel, any job); the
    panel is ~280 px wide (owner's screen, 1920x1080)."""
    if not any(norm(w.text).startswith(("jornada", "pagadas", "tareas")) for w in words):
        return None
    btn = [w for w in words if norm(w.text).startswith("salida")]
    if not btn:
        return None
    b = min(btn, key=lambda w: w.y)
    right = (b.x + b.w) / scale + origin[0] + 14
    top = b.y / scale + origin[1] - 25
    x0 = int(max(origin[0], right - 290))
    return (x0, int(max(origin[1], top)), int(right - x0), 420)


# ---------------------------------------------------------------------------
# The "Objetos" window: invisible bullets (owner, 2026-11-12)
# ---------------------------------------------------------------------------
# The game has a glitch: some trash (bullets) can't be seen in the room, so it
# has no marker. The owner keeps the game's "Objetos" window open with "bala"
# typed in its search box; the list under the box shows every bullet in the
# room. The first "bala" (in the box) is only the search text - the helper
# clicks the first "Bala" of the LIST below it.
def dark_text_for_ocr(img: np.ndarray, scale: float = 4.0) -> np.ndarray:
    """Light text on the game's dark windows -> black on white, enlarged."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    b = cv2.copyMakeBorder(b, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)


def _words_in(img, box, ocr_words, psm: int, scale: float = 4.0):
    """OCR words of img[box] with screen-picture coordinates (x, y = centre)."""
    x, y, w, h = box
    x, y = max(0, x), max(0, y)
    crop = img[y:y + h, x:x + w]
    if crop.size == 0:
        return []
    out = []
    for wd in ocr_words(dark_text_for_ocr(crop, scale), psm):
        cx = x + (wd.x + wd.w / 2 - 20) / scale
        cy = y + (wd.y + wd.h / 2 - 20) / scale
        out.append((wd.text, int(cx), int(cy), wd))
    return out


def find_objetos_search(img: np.ndarray, words, ocr_words) -> Optional[Point]:
    """Centre of the "bala" typed in the Objetos window's search box.
    words: OCR words of the whole picture (.x/.y/.w/.h in picture px). Only a
    "bala" with the window title "Objetos" just above it counts."""
    for wd in words:
        if norm(wd.text).strip(".,:;") != "bala":
            continue
        cx, cy = int(wd.x + wd.w / 2), int(wd.y + wd.h / 2)
        title = _words_in(img, (int(wd.x) - 30, cy - 60, 190, 42), ocr_words, 6)
        if any(norm(t).startswith("objeto") for t, _, _, _ in title):
            return cx, cy
    return None


def objetos_entries(img: np.ndarray, search: Point, ocr_words) -> List[Point]:
    """The "Bala" lines of the list under the search box, top first."""
    sx, sy = search
    found = _words_in(img, (sx - 25, sy + 22, 140, 260), ocr_words, 6)   # inside the window only
    return sorted(((x, y) for t, x, y, _ in found if norm(t).strip(".,:;") == "bala"),
                  key=lambda p: p[1])


# ---------------------------------------------------------------------------
# What a click selected: the name above the yellow button
# ---------------------------------------------------------------------------
def selected_name(img: np.ndarray, btn: Box, ocr_words) -> str:
    """The object's name in the little window right above its yellow button
    ("Armero para Pistolas" above "Colocar")."""
    x, y, w, h = btn
    return " ".join(t for t, _, _, _ in _words_in(img, (x, y - 40, w, 32), ocr_words, 6))


def is_gun_rack(name: str) -> bool:
    """Owner (2026-11-12): the ammunition goes on the "Armero para Pistolas" -
    SK sometimes selected the desk under it instead."""
    n = norm(name)
    return "armero" in n and "pistol" in n
