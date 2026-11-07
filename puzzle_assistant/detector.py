"""Finds the "Montar un arma" title on screen.

Discovery (slow, OCR) runs only until the title has been seen once. Its
appearance is then stored as a template and every later check is a cheap
OpenCV template match on a downscaled frame (a few milliseconds).
"""
from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import DATA_DIR, Config
from .ocr import Tesseract, Word
from .parser import norm
from .vision import Rect, prep_for_ocr

TEMPLATE_PATH = os.path.join(DATA_DIR, "title_template.png")


def _title_match(words: List[Word], keywords: List[str]) -> Optional[Rect]:
    """Find consecutive OCR words spelling one of the keywords (fuzzy)."""
    import difflib
    toks = [norm(w.text) for w in words]
    for kw in keywords:
        parts = norm(kw).split()
        n = len(parts)
        for i in range(len(words) - n + 1):
            seg = words[i:i + n]
            if len({w.line for w in seg}) != 1:
                continue
            joined = " ".join(toks[i:i + n])
            if difflib.SequenceMatcher(None, joined, " ".join(parts)).ratio() >= 0.8:
                x0 = min(w.x for w in seg)
                y0 = min(w.y for w in seg)
                x1 = max(w.x + w.w for w in seg)
                y1 = max(w.y + w.h for w in seg)
                return (x0, y0, x1 - x0, y1 - y0)
    return None


class TitleDetector:
    MATCH_THRESHOLD = 0.80

    def __init__(self, cfg: Config, ocr: Tesseract):
        self.cfg = cfg
        self.ocr = ocr
        self.scale = cfg.detect_scale
        self.template: Optional[np.ndarray] = None       # gray, at match scale k
        self.template_full: Optional[np.ndarray] = None  # gray, full resolution
        self.k = self.scale
        self.last_loc: Optional[Tuple[int, int]] = None
        self.last_discovery = 0.0
        self._alt_scales = [1.0, 0.9, 1.1, 0.8, 1.25, 0.67, 1.5]
        self._scale_idx = 0
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(TEMPLATE_PATH):
            t = cv2.imread(TEMPLATE_PATH, cv2.IMREAD_GRAYSCALE)
            if t is not None and t.size > 0:
                self._set_template(t)

    def _set_template(self, full_res: np.ndarray) -> None:
        """Keep the template at full resolution on disk; match at detect scale
        when the title is big enough, otherwise at full resolution."""
        small = cv2.resize(full_res, None, fx=self.scale, fy=self.scale,
                           interpolation=cv2.INTER_AREA)
        if small.shape[0] >= 8 and small.shape[1] >= 24:
            self.template, self.k = small, self.scale
        else:
            self.template, self.k = full_res, 1.0
        self.template_full = full_res

    def forget(self) -> None:
        self.template = None
        self.template_full = None
        self.last_loc = None
        try:
            os.remove(TEMPLATE_PATH)
        except OSError:
            pass

    def learn(self, full_gray: np.ndarray, title: Rect) -> None:
        x, y, w, h = title
        pad = max(2, h // 4)
        crop = full_gray[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad].copy()
        self._set_template(crop)
        self.last_loc = None
        os.makedirs(DATA_DIR, exist_ok=True)
        cv2.imwrite(TEMPLATE_PATH, crop)

    # -- detection ---------------------------------------------------------
    def discover(self, full_gray: np.ndarray) -> Optional[Rect]:
        """Slow path: OCR the frame looking for the title text."""
        self.last_discovery = time.monotonic()
        H, W = full_gray.shape[:2]
        img = full_gray
        f = 1.0
        if W > 2200:  # 4K screens: halve first, text is big enough
            f = 0.5
            img = cv2.resize(full_gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        elif W * H < 900_000:  # small user-selected region: upscale small game fonts
            f = 2.0
            img = cv2.resize(full_gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        g = prep_for_ocr(img, scale=1.0, pad=0)
        words = self.ocr.words(g, psm=11)
        r = _title_match(words, self.cfg.title_keywords)
        if r is None:
            return None
        x, y, w, h = r
        return (int(x / f), int(y / f), int(w / f), int(h / f))

    def match(self, full_gray: np.ndarray, small_gray: np.ndarray) -> Optional[Rect]:
        """Fast path: template match. Returns title rect in full-res coords."""
        t = self.template
        if t is None:
            return None
        k = self.k
        frame = small_gray if k != 1.0 else full_gray

        # 1) around the last known position, 2) whole frame
        if self.last_loc is not None:
            lx, ly = self.last_loc
            th, tw = t.shape[:2]
            x0, y0 = max(0, lx - tw), max(0, ly - th * 2)
            roi = frame[y0:y0 + th * 5, x0:x0 + tw * 3]
            if roi.shape[0] >= th and roi.shape[1] >= tw:
                res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(res)
                if mv >= self.MATCH_THRESHOLD:
                    return self._hit(x0 + ml[0], y0 + ml[1], t, k)
        if frame.shape[0] < t.shape[0] or frame.shape[1] < t.shape[1]:
            return None
        res = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv >= self.MATCH_THRESHOLD:
            return self._hit(ml[0], ml[1], t, k)

        # 3) one alternative zoom level per call (browser zoom / DPI changed)
        s = self._alt_scales[self._scale_idx % len(self._alt_scales)]
        self._scale_idx += 1
        if s != 1.0:
            ts = cv2.resize(t, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if frame.shape[0] >= ts.shape[0] and frame.shape[1] >= ts.shape[1] and ts.shape[1] > 8:
                res = cv2.matchTemplate(frame, ts, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(res)
                if mv >= self.MATCH_THRESHOLD + 0.05:
                    self.template = ts  # adopt the new zoom level
                    return self._hit(ml[0], ml[1], ts, k)
        return None

    def _hit(self, x: int, y: int, t: np.ndarray, k: float) -> Rect:
        self.last_loc = (x, y)
        th, tw = t.shape[:2]
        return (int(x / k), int(y / k), int(tw / k), int(th / k))
