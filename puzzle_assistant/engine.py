"""Background monitoring loop.

Pipeline per tick (only reads the screen, never sends any input):
  grab -> find the grid of piece cards (no OCR) -> card hashes
  -> skip if same puzzle as already shown -> parallel OCR -> parse -> solve
"""
from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from typing import List, Optional

import cv2

from .analyzer import Analysis, Analyzer, card_hashes, describe
from .capture import ScreenGrabber
from .config import DEBUG_DIR, Config
from .ocr import OcrError, Tesseract
from .locate import Locator
from .vision import hamming, to_gray

CARD_CHANGED_BITS = 10   # dhash distance meaning "this card looks different"
SAME_PUZZLE_MAX_CHANGED = 2


def same_layout(a: List[int], b: List[int]) -> bool:
    if not a or not b or len(a) != len(b):
        return False
    if len(a) == 1:
        return hamming(a[0], b[0]) <= 12
    changed = sum(1 for x, y in zip(a, b) if hamming(x, y) > CARD_CHANGED_BITS)
    return changed <= SAME_PUZZLE_MAX_CHANGED


class Engine(threading.Thread):
    def __init__(self, cfg: Config, out: "queue.Queue[dict]"):
        super().__init__(daemon=True, name="puzzle-engine")
        self.cfg = cfg
        self.out = out
        self._stop = threading.Event()
        self._rescan = threading.Event()
        self._snapshot = threading.Event()
        self._paused = threading.Event()
        self.region_changed = threading.Event()

    # -- commands from the UI (thread-safe) ---------------------------------
    def stop(self) -> None:
        self._stop.set()

    def rescan(self) -> None:
        self._rescan.set()

    def snapshot(self) -> None:
        self._snapshot.set()

    def set_paused(self, paused: bool) -> None:
        (self._paused.set if paused else self._paused.clear)()

    def _emit(self, **kw) -> None:
        self.out.put(kw)

    # -- main loop ---------------------------------------------------------
    def run(self) -> None:
        try:
            ocr = Tesseract(self.cfg)
        except OcrError as exc:
            self._emit(state="error", msg=str(exc))
            return
        grabber = ScreenGrabber(self.cfg.monitor)
        analyzer = Analyzer(self.cfg, ocr)
        locator = Locator(self.cfg)
        self._emit(state="waiting", msg="Waiting for puzzle…")

        active = False
        last_seen = 0.0
        shown: Optional[Analysis] = None
        shown_hashes: List[int] = []
        tried_hashes: List[int] = []
        last_analysis: Optional[Analysis] = None

        while not self._stop.is_set():
            tick = time.monotonic()
            if self._paused.is_set():
                time.sleep(0.2)
                continue
            try:
                force = self._rescan.is_set()
                self._rescan.clear()
                if self.region_changed.is_set():
                    self.region_changed.clear()
                    locator.reset()
                if force:
                    locator.reset()

                frame, _ = grabber.grab(self.cfg.region)
                gray = to_gray(frame)
                hit = locator.find(frame, gray)

                if self._snapshot.is_set():
                    self._snapshot.clear()
                    self._save_snapshot(frame, last_analysis)

                if hit is None:
                    if active and tick - last_seen > self.cfg.disappear_after_ms / 1000.0:
                        active, shown, shown_hashes, tried_hashes = False, None, [], []
                        self._emit(state="waiting", msg="Waiting for puzzle…")
                    elif force:
                        self._emit(state="waiting", msg="No puzzle found on screen")
                    self._sleep(tick, idle=True)
                    continue

                last_seen = tick
                (x, y, w, h), cards = hit
                panel = frame[y:y + h, x:x + w]
                hashes = card_hashes(panel, cards)

                if not force:
                    if shown is not None and same_layout(hashes, shown_hashes):
                        self._sleep(tick)
                        continue
                    if tried_hashes and hashes == tried_hashes:  # unchanged image
                        self._sleep(tick)
                        continue

                if not active or force:
                    self._emit(state="analyzing", msg="Puzzle detected – reading…")
                active = True
                a = analyzer.analyze_panel(panel, cards)
                last_analysis = a
                tried_hashes = hashes

                certain = bool(a.solution and a.solution.certain)
                if shown is not None and shown.solution and shown.solution.certain and not certain:
                    # keep the confirmed answer (pieces are probably being mounted)
                    shown_hashes = hashes
                elif certain or shown is None or not (shown.solution and shown.solution.certain) or force:
                    if certain:
                        shown, shown_hashes = a, hashes
                    self._emit(state="detected", analysis=a, msg="Puzzle detected")
            except Exception as exc:
                self._emit(state="error", msg=f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
                time.sleep(1.0)
            self._sleep(tick)
        grabber.close()

    def _sleep(self, tick: float, idle: bool = False) -> None:
        ms = self.cfg.idle_poll_ms if idle else self.cfg.poll_interval_ms
        delay = ms / 1000.0 - (time.monotonic() - tick)
        if delay > 0:
            self._stop.wait(delay)

    def _save_snapshot(self, frame, analysis: Optional[Analysis]) -> None:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(DEBUG_DIR, f"snapshot-{stamp}.png")
        cv2.imwrite(path, frame)
        if analysis is not None:
            with open(os.path.join(DEBUG_DIR, f"snapshot-{stamp}.txt"), "w", encoding="utf-8") as fh:
                fh.write(describe(analysis))
        self._emit(state="info", msg=f"Snapshot saved to debug\\snapshot-{stamp}.png")
