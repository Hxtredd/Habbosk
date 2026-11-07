"""Reading the game's question window ('Estudiar en la biblioteca' and the
general-knowledge task).

    Pregunta 2 de 4                         +1
    ADIVINANZA                     <- yellow category label
    Parezco un regalo, pero ...    <- the question (bold)
    • Son dos ratos seguidos.      <- clue lines (hints, numbered clues)
    [ La espada de Aquiles     ]   <- options: one column (choice / order)
    [ El escudo de Esparta  ✕  ]      or two columns (pairs)
    [ El caballo de Troya   ✓  ]   <- after answering: green = correct
    [ No era esa ... explanation ] <- feedback box (explanation)

Found by its column(s) of equal-width option boxes with a yellow category
label above them, so chat, notifications and other windows are ignored.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..vision import Rect, dhash, prep_for_ocr, rect_candidates, to_gray
from .mathread import looks_like_math, read_math
from .model import CHOICE, ORDER, PAIRS, Answer, Question
from .text import guess_language, normalize


@dataclass
class Layout:
    boxes: List[Rect]                 # options (choice / order), top to bottom
    left: List[Rect] = field(default_factory=list)    # pairs
    right: List[Rect] = field(default_factory=list)
    category: Optional[Rect] = None   # yellow label line
    feedback: Optional[Rect] = None   # explanation box after answering
    col: Rect = (0, 0, 0, 0)          # x, y, w, h of the option column(s)

    @property
    def pairs(self) -> bool:
        return bool(self.left)

    def all_boxes(self) -> List[Rect]:
        return self.left + self.right if self.pairs else self.boxes


@dataclass
class Screen:
    question: Question
    answered: bool                    # the game shows the result (feedback box)
    status: List[str]                 # per option: "correct" | "wrong" | ""
    revealed: Optional[Answer]        # the correct answer as shown by the game
    verdict: str = ""                 # "No era esa..." / "¡Bien!..."
    explanation: str = ""
    layout: Optional[Layout] = None
    # an option looked like a number / sum but couldn't be read with
    # certainty: the built-in solvers must not answer from this reading
    text_unsure: bool = False
    # ORDENAR while answering: how many steps the player has already tapped
    progress: int = 0


# ---------------------------------------------------------------------------
# Finding the window
# ---------------------------------------------------------------------------
def _inside(outer: Rect, inner: Rect, lo: int = 1, hi: int = 9) -> bool:
    dx0, dy0 = inner[0] - outer[0], inner[1] - outer[1]
    dx1 = outer[0] + outer[2] - inner[0] - inner[2]
    dy1 = outer[1] + outer[3] - inner[1] - inner[3]
    return all(lo <= d <= hi for d in (dx0, dx1)) and dy0 >= lo - 1 and dy1 >= lo - 1


def _norm_boxes(rects: List[Rect]) -> List[Rect]:
    """Each option border gives an inner and an outer edge, and the outer
    edges of neighbouring options can merge into one tall rectangle. The
    inner edges never touch, so they are used; an outer edge whose inner one
    was not found is shrunk to the inner size."""
    rects = list(dict.fromkeys(rects))
    keep = []
    for r in rects:
        if any(o != r and _inside(r, o) and abs((r[2] - o[2]) - 8) <= 4 for o in rects):
            continue                         # an outer ring (or merged rings): use the inner ones
        keep.append(r)
    out = []
    for x, y, w, h in keep:
        # an outer ring is 8 px wider than the inner boxes of its column
        if any(abs(o[2] - (w - 8)) <= 3 and abs(o[0] - (x + 4)) <= 3 for o in keep):
            x, y, w, h = x + 4, y + 4, w - 8, h - 8
        out.append((x, y, w, h))
    return out


def _columns(boxes: List[Rect]) -> List[List[Rect]]:
    """Boxes stacked in one column: same x and width, small vertical gaps."""
    cols: List[List[Rect]] = []
    for b in sorted(boxes, key=lambda r: (r[0], r[1])):
        for col in cols:
            c = col[-1]
            if abs(b[0] - c[0]) <= 6 and abs(b[2] - c[2]) <= max(6, 0.03 * c[2]):
                col.append(b)
                break
        else:
            cols.append([b])
    good = []
    for col in cols:
        col.sort(key=lambda r: r[1])
        hmed = float(np.median([r[3] for r in col]))
        col = [r for r in col if r[3] <= 1.8 * hmed]       # the feedback box is much taller
        run = [col[0]]
        for b in col[1:]:
            gap = b[1] - (run[-1][1] + run[-1][3])
            if -2 <= gap <= 0.8 * hmed:
                run.append(b)
            elif len(run) >= 3:
                break
            else:
                run = [b]
        if len(run) >= 3:
            good.append(run)
    return good


def _yellow_lines(img: np.ndarray) -> List[Tuple[int, int, int]]:
    """Rows of yellow text: (y0, y1, number of yellow components)."""
    b, g, r = [img[..., i].astype(np.int16) for i in range(3)]
    m = (r > 170) & (g > 110) & (b < 110) & (r - b > 90)
    rows = m.sum(axis=1)
    lines, y = [], 0
    while y < len(rows):
        if rows[y] >= 2:
            y0 = y
            while y < len(rows) and rows[y] >= 1:
                y += 1
            n, _, st, _ = cv2.connectedComponentsWithStats(m[y0:y].astype(np.uint8), 8)
            lines.append((y0, y, n - 1))
        y += 1
    return lines


def locate_question(frame: np.ndarray, gray: Optional[np.ndarray] = None) -> Optional[Layout]:
    gray = to_gray(frame) if gray is None else gray
    H, W = gray.shape[:2]
    all_rects = rect_candidates(gray, 600, H * W * 0.5)
    rects = [r for r in all_rects if 2.6 <= r[2] / max(1, r[3]) <= 16 and r[3] >= 18]
    boxes = _norm_boxes(rects)
    cols = _columns(boxes)
    if not cols:
        return None
    # two columns side by side with aligned rows = pairs
    cols.sort(key=lambda c: (c[0][1], c[0][0]))
    candidates: List[Layout] = []
    used = set()
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            if i == j or i in used or j in used:
                continue
            if b[0][0] <= a[0][0]:
                continue
            gap = b[0][0] - (a[0][0] + a[0][2])
            same = abs(a[0][2] - b[0][2]) <= 6 and len(a) == len(b) and -2 <= gap <= 0.15 * a[0][2]
            if same and all(abs(p[1] - q[1]) <= 6 for p, q in zip(a, b)):
                x0, y0 = a[0][0], min(a[0][1], b[0][1])
                x1, y1 = b[0][0] + b[0][2], max(a[-1][1] + a[-1][3], b[-1][1] + b[-1][3])
                candidates.append(Layout([], a, b, col=(x0, y0, x1 - x0, y1 - y0)))
                used.update((i, j))
    for i, a in enumerate(cols):
        if i not in used and 3 <= len(a) <= 8:
            x0, y0 = a[0][0], a[0][1]
            candidates.append(Layout(a, col=(x0, y0, a[0][2], a[-1][1] + a[-1][3] - y0)))
    for lay in candidates:
        x, y, w, h = lay.col
        hb = int(np.median([r[3] for r in lay.all_boxes()]))
        top = max(0, y - 9 * hb)
        region = frame[top:y, x:x + int(w * 0.7)]
        lines = [ln for ln in _yellow_lines(region) if ln[2] >= 4 and ln[1] - ln[0] >= 5]
        if not lines:
            continue
        y0, y1, _ = lines[-1]                       # the label closest above the options
        lay.category = (x, top + y0 - 2, int(w * 0.7), y1 - y0 + 4)
        lay.feedback = _feedback_box(all_rects, lay, hb)
        return lay
    return None


def _feedback_box(rects: List[Rect], lay: Layout, hb: int) -> Optional[Rect]:
    x, y, w, h = lay.col
    bottom = y + h
    for r in sorted(rects, key=lambda r: r[1]):
        if abs(r[0] - x) <= 8 and abs(r[2] - w) <= 12 and bottom - 4 <= r[1] <= bottom + 2 * hb \
                and r[3] >= 1.8 * hb:
            return r
    return None


def fingerprint(frame: np.ndarray, lay: Layout) -> tuple:
    """Changes when the question, the options or the result change - not on
    the timer, the mouse hovering over an option, or the hint counter."""
    x, y, w, h = lay.col
    cy = lay.category[1] if lay.category else y
    q = frame[cy:y, x:x + w]
    parts = [dhash(q, 16) if q.size else 0]
    for bx, by, bw, bh in lay.all_boxes():
        inner = frame[by + 6:by + bh - 6, bx + 8:bx + bw - 8]
        parts.append(dhash(inner) if inner.size else 0)
    # the line under the options ("Toca el paso 2.") changes with each correct tap
    st = _step_region(frame, lay)
    parts.append(dhash(st) if st.size else 0)
    return tuple(parts) + (lay.feedback is not None, tuple(_status(frame, b) for b in
                                                            lay.all_boxes()))


def _step_region(frame: np.ndarray, lay: Layout) -> np.ndarray:
    x, y, w, h = lay.col
    return frame[y + h + 4:y + h + 30, max(0, x - 12):x + int(w * 0.7)]


_STEP_RE = re.compile(r"paso\s*(\d{1,2})", re.I)


def _read_step(ocr, frame: np.ndarray, lay: Layout) -> Optional[int]:
    """'Toca el paso 2.' under ORDENAR options -> 2 (the step to tap next)."""
    m = _STEP_RE.search(" ".join(_ocr_lines(ocr, _step_region(frame, lay), 7, 2.5)))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Reading it
# ---------------------------------------------------------------------------
def _ring(frame: np.ndarray, r: Rect) -> np.ndarray:
    x, y, w, h = r
    H, W = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x - 2), max(0, y - 2), min(W, x + w + 2), min(H, y + h + 2)
    crop = frame[y0:y1, x0:x1].astype(np.int16)
    t = 5
    return np.concatenate([crop[:t].reshape(-1, 3), crop[-t:].reshape(-1, 3),
                           crop[:, :t].reshape(-1, 3), crop[:, -t:].reshape(-1, 3)])


def _status(frame: np.ndarray, r: Rect) -> str:
    """Border colour: green = correct, red = wrong pick."""
    ring = _ring(frame, r)
    b, g, rr = ring[:, 0], ring[:, 1], ring[:, 2]
    green = float(np.percentile(g - np.maximum(rr, b), 90))
    red = float(np.percentile(rr - np.maximum(g, b), 90))
    if green > 18 and green > red:
        return "correct"
    if red > 22:
        return "wrong"
    return ""


def _hue(frame: np.ndarray, r: Rect) -> Optional[float]:
    """Dominant border hue (pairs are revealed with one colour per pair)."""
    ring = _ring(frame, r).astype(np.uint8).reshape(-1, 1, 3)
    hsv = cv2.cvtColor(ring, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    sat = hsv[(hsv[:, 1] > 70) & (hsv[:, 2] > 80)]
    if len(sat) < 10:
        return None
    ang = sat[:, 0].astype(np.float64) * (np.pi / 90.0)   # OpenCV hue 0..179
    return float(np.degrees(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360)


_CLUE_RE = re.compile(r"^\s*(?:[•·●▪\-–*+]|\d+[.)])\s*")
# junk left of an option's text: an empty step circle read as 'O', '©', '()'...
_LEAD_RE = re.compile(r"^\s*(?:[O0o@©®()\[\]|]{1,2})\s+")


def _ocr_lines(ocr, img: np.ndarray, psm: int = 6, scale: float = 2.0) -> List[str]:
    if img.size == 0:
        return []
    text = ocr.text(prep_for_ocr(img, scale=scale), psm=psm)
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _tight(img: np.ndarray, pad: int = 6):
    """Crop to the text itself (Tesseract misreads small text in a big empty box)."""
    g = to_gray(img).astype(np.int16)
    m = np.abs(g - int(np.median(g))) > 40
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None, 0
    crop = img[max(0, ys.min() - pad):ys.max() + pad + 1, max(0, xs.min() - pad):xs.max() + pad + 1]
    rows = m[ys.min():ys.max() + 1].any(axis=1)
    lines = 1 + int(np.sum(rows[1:] & ~rows[:-1]))       # separate bands of ink
    return crop, (xs.max() - xs.min() + 1, ys.max() - ys.min() + 1, lines)


_WL_GLYPH = ["-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]


def _ocr_option(ocr, img: np.ndarray) -> str:
    crop, info = _tight(img)
    if crop is None:
        return ""
    w, h, lines = info
    if w <= 1.6 * h:                         # a single letter / digit (e.g. 'U', 'X', '7')
        words = ocr.words(prep_for_ocr(crop, scale=3.0), psm=10, extra=_WL_GLYPH)
        txt = "".join(x.text for x in words).strip()
        if txt:
            return txt
    if lines <= 1:
        return " ".join(_ocr_lines(ocr, crop, 7, 2.0))
    return " ".join(_ocr_lines(ocr, crop, 6, 2.5))


CATEGORIES = ["ADIVINANZA", "PREGUNTA", "ORDENAR", "PAREJAS", "ENCUENTRA EL ERROR",
              "¿QUIÉN SOY?", "VERDADERO O FALSO", "COMPLETA LA FRASE"]


def snap_category(raw: str) -> str:
    """Correct small OCR slips ('DRDENAR' -> 'ORDENAR') against known labels."""
    import difflib
    n = normalize(raw)
    keys = {normalize(c): c for c in CATEGORIES}
    m = difflib.get_close_matches(n, list(keys), n=1, cutoff=0.75)
    return keys[m[0]] if m else raw.upper()


def _clean(s: str) -> str:
    s = s.replace("|", "I").strip(" .·•-–—_;,")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def split_question(lines: List[str]) -> Tuple[str, List[str]]:
    """Question text vs clue lines (bullets, numbered clues, a sequence after
    the question mark such as '¿Qué letra sigue? F, J, N, R, ?')."""
    q_lines, clues = [], []
    for ln in lines:
        if _CLUE_RE.match(ln) and q_lines:
            clues.append(_CLUE_RE.sub("", ln).strip())
        elif _CLUE_RE.match(ln) and not q_lines and re.match(r"^\s*\d+[.)]", ln):
            clues.append(_CLUE_RE.sub("", ln).strip())       # '1. Soy un asistente NPC.'
        else:
            q_lines.append(ln)
    q = " ".join(q_lines)
    m = re.match(r"^(.*?\?)\s+(.+)$", q)
    if m and "," in m.group(2):
        q, seq = m.group(1), m.group(2)
        clues.insert(0, seq.strip())
    clues = [_clean(c) for c in clues if _clean(c)]
    if not q.strip() and clues:
        # '¿QUIÉN SOY?' has no question line: its first numbered clue is the question
        q, clues = clues[0], clues[1:]
    return _clean(q) if q else "", clues


def read_question(frame: np.ndarray, lay: Layout, ocr, pool: Optional[ThreadPoolExecutor] = None,
                  learn_only_if_answered: bool = True) -> Screen:
    x, y, w, h = lay.col
    own_pool = pool is None
    pool = pool or ThreadPoolExecutor(max_workers=8)
    try:
        cat_r = lay.category
        xl = max(0, x - 12)                       # the labels start left of the boxes
        cat_img = frame[cat_r[1]:cat_r[1] + cat_r[3], xl:cat_r[0] + cat_r[2]]
        q_img = frame[cat_r[1] + cat_r[3]:y - 2, xl:x + w]
        f_q = pool.submit(_ocr_lines, ocr, q_img, 6, 2.0)
        # the category decides how the options are cropped: read it first
        category = snap_category(_clean(" ".join(_ocr_lines(ocr, cat_img, 7, 2.0))))
        boxes = lay.all_boxes()
        order_like = not lay.pairs and normalize(category).startswith("orden")
        f_opts, f_nums, f_math = [], [], []
        for bx, by, bw, bh in boxes:
            # text only: skip the ✓/✕ icon on the right and the step badge on the left
            left = bx + (int(bh * 0.95) if order_like else 6)
            right = bx + bw - (6 if lay.pairs else 30)
            crop = frame[by + 3:by + bh - 3, left:right]
            f_opts.append(pool.submit(_ocr_option, ocr, crop))
            f_math.append(None if lay.pairs else pool.submit(read_math, crop, ocr))
            if order_like:
                f_nums.append(_badge_number(frame[by:by + bh, bx:bx + bw], bh))
        f_step = (pool.submit(_read_step, ocr, frame, lay)
                  if order_like and lay.feedback is None else None)
        f_fb = None
        if lay.feedback is not None:
            fx, fy, fw, fh = lay.feedback
            f_fb = pool.submit(_ocr_lines, ocr, frame[fy + 4:fy + fh - 4, fx + 6:fx + fw - 6], 6, 2.0)

        text, clues = split_question(f_q.result())
        raw_opts = [_clean(f.result()) for f in f_opts]
        # numbers and sums: the shape-based reader is exact (÷, lost digits);
        # where it can't be sure, say so instead of trusting general OCR
        text_unsure = False
        for i, fm in enumerate(f_math):
            exact = fm.result() if fm is not None else None
            if exact is not None:
                raw_opts[i] = exact
            elif fm is not None and looks_like_math(_LEAD_RE.sub("", raw_opts[i]) if order_like
                                                    else raw_opts[i]):
                text_unsure = True
        cat_n = normalize(category)
        kind = PAIRS if lay.pairs else (ORDER if cat_n.startswith("orden") else CHOICE)

        options = [_clean(_LEAD_RE.sub("", s)) if kind == ORDER else s for s in raw_opts]
        numbers: List[Optional[int]] = list(f_nums) or [None] * len(options)

        if kind == PAIRS:
            n = len(lay.left)
            q = Question(category, text, options[:n], PAIRS, clues, options[n:])
        else:
            q = Question(category, text, options, kind, clues)
        q.language = guess_language(text + " " + " ".join(options))

        status = [_status(frame, b) for b in boxes]
        # the game shows the result: explanation box, or a green (correct) option
        answered = lay.feedback is not None or (kind == CHOICE and "correct" in status)
        verdict, explanation = "", ""
        if f_fb is not None:
            verdict, explanation = _split_feedback(f_fb.result())
        revealed = _revealed(frame, lay, q, status, numbers) if answered else None
        if revealed is not None:
            revealed.explanation = explanation
        progress = 0
        if f_step is not None and not answered:
            step = f_step.result()
            if step is not None and 1 <= step <= len(q.options) + 1:
                progress = step - 1
            else:                       # fall back to the numbers shown in tapped options
                progress = sum(1 for n in numbers if n is not None)
        return Screen(q, answered, status, revealed, verdict, explanation, lay, text_unsure,
                      progress)
    finally:
        if own_pool:
            pool.shutdown(wait=False)


def _badge_number(box_img: np.ndarray, bh: int) -> Optional[int]:
    """The step number in the round badge of an ORDENAR option (white digit on
    a dark circle, shown after answering). Read with the glyph matcher."""
    from ..glyphs import classify, components
    c = box_img[:, 6:int(bh * 0.95)]
    if c.size == 0:
        return None
    g = to_gray(c).astype(np.int16)
    ink = np.clip(g - 150, 0, 255).astype(np.uint8)
    if ink.max() < 40:
        return None                       # empty circle: not revealed yet
    cs = [b for b in components(ink > 0.5 * ink.max()) if b[3] >= 0.18 * bh]
    if not cs:
        return None
    x, y, w, h = max(cs, key=lambda b: b[3])
    ch, score, margin = classify(ink[max(0, y - 1):y + h + 1, max(0, x - 1):x + w + 1], "123456789")
    return int(ch) if score >= 0.7 and margin >= 0.08 else None


_STOP = ("siguiente", "terminar", "esta ronda", "hoy ya estudiaste", "inteligencia")


def _is_button_word(word: str) -> bool:
    """'Siguiente' / 'Terminar' button text, also when misread ('Siguierjip'),
    but not ordinary words such as 'sigue'."""
    import difflib
    n = normalize(word)
    if len(n) < 7:
        return False
    if n.startswith(("siguie", "termin")):
        return True
    return any(difflib.SequenceMatcher(None, n, k).ratio() >= 0.8 for k in ("siguiente", "terminar"))


def _is_stop(line: str) -> bool:
    n = normalize(line)
    if n.startswith(_STOP):
        return True
    words = line.split()
    return len(words) <= 3 and any(_is_button_word(w) for w in words)


def _cut_at_button(line: str) -> str:
    """'...alimento. I Siguierjip' -> '...alimento.' (button text on the same line)."""
    words = line.split()
    for i, wd in enumerate(words):
        if _is_button_word(wd):
            return " ".join(words[:i]).rstrip(" I|")
    return line


def _split_feedback(lines: List[str]) -> Tuple[str, str]:
    if not lines:
        return "", ""
    verdict = _clean(lines[0])
    expl = []
    for ln in lines[1:]:
        if _is_stop(ln):
            break
        cut = _cut_at_button(ln)
        expl.append(cut)
        if cut != ln:
            break
    text = _clean(" ".join(expl))
    # stray single characters at the end (button edges, icons)
    text = re.sub(r"(?:\s+[A-Za-z|]{1,2})+$", "", text).strip()
    return verdict, text


def _revealed(frame, lay: Layout, q: Question, status: List[str],
              numbers: List[Optional[int]]) -> Optional[Answer]:
    """The correct answer as the game shows it after answering."""
    if q.kind == CHOICE:
        good = [o for o, s in zip(q.options, status) if s == "correct"]
        if len(good) == 1:
            return Answer(CHOICE, choice=good[0], source="game", verified=True, confidence=1.0)
        return None
    if q.kind == ORDER:
        if None in numbers or sorted(numbers) != list(range(1, len(numbers) + 1)):
            return None
        order = [o for _, o in sorted(zip(numbers, q.options))]
        return Answer(ORDER, order=order, source="game", verified=True, confidence=1.0)
    # pairs: same border colour = same pair
    lh = [_hue(frame, b) for b in lay.left]
    rh = [_hue(frame, b) for b in lay.right]
    if None in lh or None in rh:
        return None
    pairs, taken = {}, set()
    for i, a in enumerate(lh):
        dists = [(min(abs(a - b), 360 - abs(a - b)), j) for j, b in enumerate(rh) if j not in taken]
        d, j = min(dists)
        if d > 18:
            return None
        taken.add(j)
        pairs[q.options[i]] = q.right[j]
    return Answer(PAIRS, pairs=pairs, source="game", verified=True, confidence=1.0)
