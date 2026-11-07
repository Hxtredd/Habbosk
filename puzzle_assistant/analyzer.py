"""Panel image -> Puzzle -> Solution. Used by the live engine and offline mode."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional

import cv2
import numpy as np

from .config import DEBUG_DIR, Config
from .model import Puzzle, Solution
from .ocr import Tesseract, Word
from .model import LETTERS
from .cardreader import layout_recognised, read_card
from .parser import (build_from_readings, build_puzzle, cluster_by_anchors, finish_header,
                     letter_tokens, parse_card)
from .solver import solve
from .vision import Rect, dhash, find_big_glyph, find_cards, mask_rects, prep_for_ocr


@dataclass
class Analysis:
    puzzle: Optional[Puzzle]
    solution: Optional[Solution]
    cards: Optional[List[Rect]]
    mode: str                       # "cards" | "anchors" | "failed"
    ms: float = 0.0
    error: str = ""
    card_hashes: List[int] = field(default_factory=list)


def card_hashes(panel: np.ndarray, cards: List[Rect]) -> List[int]:
    return [dhash(panel[y:y + h, x:x + w]) for x, y, w, h in cards]


def _renumber_lines(words: List[Word]) -> None:
    """Give words on the same visual row the same line id (needed after psm 11)."""
    if not words:
        return
    med_h = sorted(w.h for w in words)[len(words) // 2] or 10
    rows: List[List[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if rows and abs(w.cy - rows[-1][-1].cy) < med_h * 0.6:
            rows[-1].append(w)
        else:
            rows.append([w])
    for i, row in enumerate(rows):
        for w in row:
            w.line = i
    words.sort(key=lambda w: (w.line, w.x))


class Analyzer:
    def __init__(self, cfg: Config, ocr: Tesseract):
        self.cfg = cfg
        self.ocr = ocr
        self.pool = ThreadPoolExecutor(max_workers=max(2, cfg.ocr_threads))

    def _ocr_card(self, crop: np.ndarray) -> List[Word]:
        g = prep_for_ocr(crop, scale=self.cfg.ocr_upscale)
        words = self.ocr.words(g, psm=6)
        if len(words) < 2:
            # block mode sometimes returns nothing for sparse cards
            alt = self.ocr.words(g, psm=11)
            if len(alt) > len(words):
                words = alt
        _renumber_lines(words)
        if parse_card(words).letter not in LETTERS:
            # Big isolated letters are often skipped in block mode: read the
            # largest glyph alone, restricted to A-H.
            r = find_big_glyph(g)
            if r is not None:
                x, y, w, h = r
                p = max(6, h // 3)
                y0, x0 = max(0, y - p), max(0, x - p)
                glyph = g[y0:y + h + p, x0:x + w + p]
                bg = int(np.median(g))
                glyph = cv2.copyMakeBorder(glyph, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=bg)
                lw = []
                for psm in (10, 8):  # psm 10 occasionally returns nothing
                    lw = self.ocr.words(glyph, psm=psm,
                                        extra=["-c", "tessedit_char_whitelist=" + LETTERS])
                    lw = [v for v in lw if len(v.text.strip()) == 1 and v.text.strip() in LETTERS]
                    if lw:
                        break
                if lw:
                    best = max(lw, key=lambda v: v.conf)
                    words.insert(0, Word(best.text.strip(), best.conf, x, y, w, h, -1))
        self._verify_letters(g, words)
        return words

    def _verify_letters(self, g: np.ndarray, words: List[Word]) -> None:
        """Tesseract reports low confidence for lone letters even when right.
        Re-read each low-confidence letter token on its own, restricted to
        A-H; if both independent reads agree the token is considered verified.
        If they disagree, the low confidence stays and the answer is flagged."""
        bg = int(np.median(g))
        for i, letters, frac in letter_tokens(words):
            w = words[i]
            if w.conf >= self.cfg.min_confidence:
                continue
            x0 = w.x + int(w.w * frac)
            p = max(4, w.h // 3)
            crop = g[max(0, w.y - p):w.y + w.h + p, max(0, x0 - 2):w.x + w.w + p]
            if crop.size == 0:
                continue
            crop = cv2.copyMakeBorder(crop, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=bg)
            again: List[str] = []
            for psm in ((10, 8) if len(letters) == 1 else (7, 8)):
                try:
                    rw = self.ocr.words(crop, psm=psm,
                                        extra=["-c", "tessedit_char_whitelist=" + LETTERS + ",; "])
                except Exception:
                    break
                again = [c for v in rw for c in v.text if c in LETTERS]
                if again:
                    break
            if again == letters:
                w.conf = max(w.conf, 90.0)

    def _ocr_line(self, img: np.ndarray) -> str:
        g = prep_for_ocr(img, scale=3.0)
        return " ".join(w.text for w in self.ocr.words(g, psm=7))

    def _ocr_sparse(self, img: np.ndarray, scale: float) -> List[Word]:
        g = prep_for_ocr(img, scale=scale)
        words = self.ocr.words(g, psm=11)
        _renumber_lines(words)
        return words

    def analyze_panel(self, panel: np.ndarray, cards: Optional[List[Rect]] = None) -> Analysis:
        t0 = time.perf_counter()
        cfg = self.cfg
        if cards is None:
            cards = find_cards(panel, cfg.expected_pieces)
        try:
            readings = [read_card(panel[y:y + h, x:x + w]) for x, y, w, h in cards] if cards else []
            if readings and layout_recognised(readings):
                # Fast path for the game's card layout: letters, calibers and
                # arrows are read with glyph templates (no OCR process).
                header_img = mask_rects(panel, cards)
                fut_header = self.pool.submit(self._ocr_sparse, header_img, 1.5)
                name_futs = []
                if cfg.read_names:
                    for (x, y, w, h), r in zip(cards, readings):
                        if r.name_box:
                            nx, ny, nw, nh = r.name_box
                            p = max(2, nh // 2)
                            crop = panel[y + max(0, ny - p):y + ny + nh + p,
                                         x + max(0, nx - p):x + nx + nw + p]
                            name_futs.append(self.pool.submit(self._ocr_line, crop))
                        else:
                            name_futs.append(None)
                names = []
                for f in name_futs:
                    try:
                        names.append(f.result() if f else "")
                    except Exception:
                        names.append("")
                puzzle = build_from_readings(readings, names, [])
                finish_header(puzzle, fut_header.result(), len(cards))
                mode = "layout"
            elif cards:
                header_img = mask_rects(panel, cards)
                fut_header = self.pool.submit(self._ocr_sparse, header_img, 1.5)
                futs = [self.pool.submit(self._ocr_card, panel[y:y + h, x:x + w])
                        for x, y, w, h in cards]
                card_words = [f.result() for f in futs]
                header_words = fut_header.result()
                puzzle = build_puzzle(card_words, header_words, cfg.expected_pieces)
                mode = "cards"
            else:
                words = self._ocr_sparse(panel, cfg.ocr_upscale)
                grouped = cluster_by_anchors(words, cfg.expected_pieces)
                if grouped is None:
                    return Analysis(None, None, None, "failed",
                                    (time.perf_counter() - t0) * 1000,
                                    "Could not separate the pieces")
                groups, header = grouped
                for g in groups:
                    _renumber_lines(g)
                puzzle = build_puzzle(groups, header, cfg.expected_pieces)
                puzzle.warnings.append("piece boxes not detected; used text layout")
                mode = "anchors"
        except Exception as exc:  # OCR failure must never crash the app
            return Analysis(None, None, cards, "failed",
                            (time.perf_counter() - t0) * 1000, f"OCR error: {exc}")

        solution = solve(puzzle, puzzle.expected or len(puzzle.pieces), cfg.min_confidence)
        a = Analysis(puzzle, solution, cards, mode, (time.perf_counter() - t0) * 1000)
        if cards:
            a.card_hashes = card_hashes(panel, cards)
        if cfg.debug_save:
            self.save_debug(panel, a)
        return a

    def save_debug(self, panel: np.ndarray, a: Analysis) -> str:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        vis = panel.copy()
        for i, (x, y, w, h) in enumerate(a.cards or []):
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 200, 255), 2)
            cv2.putText(vis, str(i), (x + 3, y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        img_path = os.path.join(DEBUG_DIR, f"{stamp}_panel.png")
        cv2.imwrite(img_path, vis)
        with open(os.path.join(DEBUG_DIR, f"{stamp}_ocr.txt"), "w", encoding="utf-8") as fh:
            fh.write(describe(a))
        return img_path


def describe(a: Analysis) -> str:
    lines = [f"mode={a.mode} time={a.ms:.0f}ms error={a.error}"]
    p, s = a.puzzle, a.solution
    if p:
        lines.append(f"target caliber={p.target_caliber} weapon={p.weapon}")
        lines.append(f"header: {p.header_text}")
        for letter in sorted(p.pieces):
            pc = p.pieces[letter]
            lines.append(f"  {letter}: name={pc.name!r} cal={pc.caliber} deps={pc.deps} "
                         f"conf={pc.confidence:.0f} mounted={pc.mounted} notes={pc.notes}")
            lines.append(f"      raw: {pc.raw_text}")
    if s:
        lines.append(f"order={' -> '.join(s.order)} done={s.done} remaining={s.remaining} "
                     f"excluded={s.excluded} doubtful={s.doubtful} ok={s.ok} certain={s.certain}")
        for pr in s.problems:
            lines.append(f"  PROBLEM: {pr}")
        for w in s.warnings:
            lines.append(f"  warning: {w}")
    return "\n".join(lines) + "\n"
