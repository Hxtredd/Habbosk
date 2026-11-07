"""Background monitoring loop.

Pipeline per tick (only reads the screen, never sends any input):
  grab -> find the grid of piece cards (no OCR) -> card hashes
  -> new puzzle? read it (glyphs + OCR) -> solve -> lock the verified answer
  -> while locked: only follow the green ✓ marks (progress), no re-reading
"""
from __future__ import annotations

import copy
import os
import queue
import threading
import time
import traceback
from typing import Dict, List, Optional

import cv2

from .analyzer import Analysis, Analyzer, describe
from .overlay import badge_mask_rect
from .capture import ScreenGrabber, display_info
from .config import DEBUG_DIR, Config
from .ocr import OcrError, Tesseract
from .locate import Locator
from .cardreader import is_mounted
from .solver import solve
from .vision import dhash, hamming, to_gray

CARD_CHANGED_BITS = 10   # dhash distance meaning "this card looks different"
BAND_CHANGED_BITS = 24   # same for the "Pistola · calibre 38" line


def header_band(panel, cards) -> Optional[int]:
    """Fingerprint of the line right above the cards ('Fusil · calibre 5.56')."""
    top = min(c[1] for c in cards)
    ch = int(sorted(c[3] for c in cards)[len(cards) // 2])
    y0 = max(0, top - int(0.6 * ch))
    if top - y0 < 4:
        return None
    x0 = min(c[0] for c in cards)
    x1 = max(c[0] + c[2] for c in cards)
    return dhash(panel[y0:top - 1, x0:x1], 16)


def same_puzzle(old_hashes: List[int], old_band: Optional[int],
                hashes: List[int], band: Optional[int], mounted: List[bool]) -> bool:
    """Still the locked puzzle? Mounted (green) cards are allowed to look
    different; every other card and the caliber line must be unchanged."""
    if len(hashes) != len(old_hashes):
        return False
    if band is not None and old_band is not None and hamming(band, old_band) > BAND_CHANGED_BITS:
        return False
    return all(m or hamming(a, b) <= CARD_CHANGED_BITS
               for a, b, m in zip(hashes, old_hashes, mounted))


def with_progress(locked: Analysis, mounted: List[bool], cfg: Config) -> Analysis:
    """Re-solve the locked reading with the game's current ✓ marks (no OCR)."""
    puzzle = copy.deepcopy(locked.puzzle)
    for letter, m in zip(sorted(puzzle.pieces), mounted):
        puzzle.pieces[letter].mounted = m
    solution = solve(puzzle, puzzle.expected or len(puzzle.pieces), cfg.min_confidence)
    # Keep the locked order and its step numbers: what is left of a valid
    # order is still valid, and the numbers on screen must not jump around.
    base = locked.solution
    solution.order = list(base.order)
    solution.numbering = dict(base.numbering) or {l: i + 1 for i, l in enumerate(base.order)}
    solution.remaining = [l for l in base.order if not puzzle.pieces[l].mounted]
    return Analysis(puzzle, solution, locked.cards, locked.mode, 0.0, "", locked.card_hashes)


def fingerprints(panel, cards) -> List[int]:
    """Card fingerprints that ignore the area where the overlay draws its step
    badge, so the badges can never be mistaken for a changed puzzle."""
    out = []
    for x, y, w, h in cards:
        card = panel[y:y + h, x:x + w].copy()
        mx, my, mw, mh = badge_mask_rect(w, h)
        card[my:my + mh, mx:mx + mw] = 0
        out.append(dhash(card))
    return out


def _key(b: dict) -> tuple:
    return (b["left"], b["top"], b["width"], b["height"])


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
        # True when the overlay can be seen by screen capture (old Windows):
        # then the engine waits for the overlay to clear before reading.
        self.overlay_in_capture = False

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
        locators: Dict[tuple, Locator] = {}      # one per screen area
        last_target: Optional[tuple] = None    # screen the puzzle was last seen on
        self._emit(state="waiting", msg="Waiting for puzzle…")

        active = False
        last_seen = 0.0
        # Locked = a verified reading of the puzzle on screen. It is kept until
        # the puzzle closes or changes; cards turning green (mounted) only
        # update the progress, they never trigger a new reading.
        locked: Optional[Analysis] = None
        locked_hashes: List[int] = []
        locked_band: Optional[int] = None
        locked_mounted: List[bool] = []
        tried: Optional[tuple] = None
        placed: Optional[dict] = None          # card positions last sent to the overlay
        settle_until = 0.0
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
                    locators.clear()
                    last_target = None
                if force:
                    for loc in locators.values():
                        loc.reset()
                    locked = None

                # Search every screen (or the region), the last known one first.
                targets = grabber.targets(self.cfg.region)
                targets.sort(key=lambda b: _key(b) != last_target)
                hit, frames = None, []
                for b in targets:
                    frame, bounds = grabber.grab_bounds(b)
                    frames.append(frame)
                    gray = to_gray(frame)
                    loc = locators.setdefault(_key(b), Locator(self.cfg))
                    hit = loc.find(frame, gray)
                    if hit is not None:
                        last_target = _key(b)
                        break

                if self._snapshot.is_set():
                    self._snapshot.clear()
                    self._save_snapshot(frames, last_analysis)

                if hit is None:
                    if active and tick - last_seen > self.cfg.disappear_after_ms / 1000.0:
                        active, locked, tried, placed = False, None, None, None
                        self._emit(state="waiting", msg="Waiting for puzzle…")
                    elif force:
                        self._emit(state="waiting", msg="No puzzle found on screen")
                    self._sleep(tick, idle=True)
                    continue

                last_seen = tick
                (x, y, w, h), cards = hit
                panel = frame[y:y + h, x:x + w]
                hashes = fingerprints(panel, cards)
                screen = (bounds["left"], bounds["top"], bounds["width"], bounds["height"])
                place = {"bounds": screen,
                         "cards": [(bounds["left"] + x + cx, bounds["top"] + y + cy, cw, ch)
                                   for cx, cy, cw, ch in cards]}
                mounted = [is_mounted(panel[cy:cy + ch, cx:cx + cw]) for cx, cy, cw, ch in cards]
                band = header_band(panel, cards)

                if locked is not None:
                    if same_puzzle(locked_hashes, locked_band, hashes, band, mounted):
                        if mounted != locked_mounted or place != placed:
                            locked_mounted, placed = mounted, place
                            a = with_progress(locked, mounted, self.cfg)
                            last_analysis = a
                            self._emit(state="detected", analysis=a, place=place,
                                       msg="Puzzle detected")
                        self._sleep(tick)
                        continue
                    locked, tried, placed = None, None, None  # a different puzzle
                    # remove the old numbers at once: they must never sit on
                    # the new puzzle's cards, not even for a moment
                    self._emit(state="overlay_clear")
                    if self.overlay_in_capture:
                        # the overlay is visible to screen capture here: give the
                        # UI time to remove it before reading the new cards
                        settle_until = tick + 0.25
                if tick < settle_until:
                    self._sleep(tick)
                    continue

                key = (tuple(hashes), tuple(mounted))
                if not force and key == tried:  # unchanged image, already read
                    self._sleep(tick)
                    continue

                if not active or force:
                    self._emit(state="analyzing", msg="Puzzle detected – reading…")
                active = True
                a = analyzer.analyze_panel(panel, cards)
                last_analysis = a
                tried = key
                if a.solution and a.solution.certain:
                    a.solution.numbering = {l: i + 1 for i, l in enumerate(a.solution.order)}
                    locked, locked_hashes, locked_band, locked_mounted = a, hashes, band, mounted
                placed = place
                self._emit(state="detected", analysis=a, place=place, msg="Puzzle detected")
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

    def _save_snapshot(self, frames, analysis: Optional[Analysis]) -> None:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for i, frame in enumerate(frames):
            suffix = "" if i == 0 else f"-screen{i + 1}"
            cv2.imwrite(os.path.join(DEBUG_DIR, f"snapshot-{stamp}{suffix}.png"), frame)
        with open(os.path.join(DEBUG_DIR, f"snapshot-{stamp}.txt"), "w", encoding="utf-8") as fh:
            fh.write(display_info())
            fh.write(f"overlay hidden from capture: {not self.overlay_in_capture}\n\n")
            if analysis is not None:
                fh.write(describe(analysis))
        self._emit(state="info", msg=f"Snapshot saved to debug\\snapshot-{stamp}.png")
