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
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import cv2

from .analyzer import Analysis, Analyzer, describe
from .overlay import badge_mask_rect
from .capture import ScreenGrabber, display_info
from .config import DEBUG_DIR, Config
from .ocr import OcrError, Tesseract
from .locate import Locator
from .money import MoneyPanel, MoneyTracker, locate_money, parse_vistos, read_legend
from .qa.screen import (CATEGORIES, fingerprint, locate_express, locate_question,
                        read_question)
from .qa.memory import answers_match
from .qa.model import ORDER
from .qa.solvers import solve_question
from .qa.text import normalize
from .cardreader import is_mounted
from .solver import solve
from .vision import dhash, find_grids, hamming, prep_for_ocr, to_gray

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


def _picked(screen) -> tuple:
    """(option the player picked, was it correct) when the game shows it."""
    q = screen.question
    if q.kind == "choice":
        wrong = [o for o, s in zip(q.options, screen.status) if s == "wrong"]
        if wrong:
            return wrong[0], False
        if screen.revealed is not None:
            return screen.revealed.choice, True
        return None, None
    v = screen.verdict.lower()
    if v.startswith("no era") or "incorrect" in v:
        return None, False
    if v.startswith("¡bien") or v.startswith("bien") or "correct" in v:
        return None, True
    return None, None


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
        self.memory = None                     # qa.memory.Memory, set by the UI
        self.ai = None                         # qa.ai.AIAnswerer, set by the UI
        self._ai_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai")

    # -- commands from the UI (thread-safe) ---------------------------------
    def stop(self) -> None:
        self._stop.set()
        self._ai_pool.shutdown(wait=False)

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
        money_pending = None                   # money puzzle seen once, not yet stable
        qstate: dict = {}                      # question being shown (see _question_tick)
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
                    if not self.cfg.weapon_puzzle:
                        continue
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
                    # No weapon puzzle: is it the money puzzle?
                    money = None
                    for b, frame_b in (zip(targets, frames) if self.cfg.money_puzzle else ()):
                        mp = locate_money(to_gray(frame_b))
                        if mp is not None:
                            money = (b, mp)
                            break
                    if money is not None:
                        # The puzzle window slides in when it opens: only start
                        # once two checks in a row agree on where it is.
                        key = (_key(money[0]), money[1].strip, money[1].input)
                        if key != money_pending:
                            money_pending = key
                            self._sleep(tick)
                            continue
                    money_pending = None
                    if money is not None:
                        last_target = _key(money[0])
                        active, locked, tried, placed = False, None, None, None
                        self._emit(state="overlay_clear")
                        self._money_loop(grabber, analyzer, money[0], money[1])
                        self._emit(state="waiting", msg="Waiting for puzzle…")
                        continue
                    # Or a quiz question?
                    qhit = None
                    for b, frame_b in (zip(targets, frames) if self.cfg.questions else ()):
                        lay = locate_question(frame_b) or locate_express(frame_b, analyzer.ocr)
                        if lay is not None:
                            qhit = (b, frame_b, lay)
                            break
                    if qhit is not None:
                        last_target = _key(qhit[0])
                        locked, tried, placed = None, None, None
                        self._question_tick(qhit, analyzer, force, qstate)
                        active, last_seen = True, tick
                        self._sleep(tick)
                        continue
                    if active and tick - last_seen > self.cfg.disappear_after_ms / 1000.0:
                        active, locked, tried, placed = False, None, None, None
                        qstate.clear()
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

    # -- quiz questions ----------------------------------------------------
    def _question_tick(self, qhit, analyzer, force: bool, st: dict) -> None:
        """Read a question once it stops moving; answer it from memory; learn
        the correct answer when the game reveals it."""
        b, frame, lay = qhit
        self._poll_ai(st)
        fp = fingerprint(frame, lay)
        # The window slides / fades in: wait until it looks the same for a few checks.
        st["stable"] = st.get("stable", 0) + 1 if fp == st.get("pending") else 0
        st["pending"] = fp
        if st["stable"] < 2 and not force:
            return
        if fp == st.get("ignored") and not force:
            return
        if fp == st.get("read") and not force:
            # already read. If nothing was found, the first read may have been
            # taken while the window was still appearing: retry a few times.
            shown = st.get("shown")
            retry = (shown is not None and shown[1] in ("unknown", "thinking")
                     and st.get("retries", 0) < 3
                     and time.monotonic() - st.get("read_at", 0.0) >= 0.9)
            if not retry:
                return
            st["retries"] = st.get("retries", 0) + 1
        else:
            st["retries"] = 0
        if self.cfg.question_mode == "manual" and not force:
            if st.get("announced") != fp:
                st["announced"] = fp
                self._emit(state="question_wait")
            return
        screen = read_question(frame, lay, analyzer.ocr, analyzer.pool)
        q = screen.question
        if q.category not in CATEGORIES:
            # no known category label: not a quiz question (e.g. the job
            # panel's task list looks like a column of options) - ignore it,
            # and don't read it again until it changes
            if st["stable"] >= 4:
                st["ignored"] = fp
            return
        st["read"] = fp
        st["read_at"] = time.monotonic()
        ox, oy = b["left"], b["top"]
        place = {"boxes": [(ox + x, oy + y, w, h) for x, y, w, h in lay.all_boxes()],
                 "n_left": len(lay.left), "kind": q.kind}
        mem = self.memory
        # Same question as already shown? Clicking options changes their look:
        # never downgrade a shown answer and don't repeat the same result.
        ident = (normalize(q.category), normalize(q.text))
        shown = st.get("shown")
        if not shown or shown[0] != ident:
            st["progress"] = screen.progress
        if shown and shown[0] == ident and not force:
            if not screen.answered and shown[1] not in ("unknown", "thinking"):
                # ORDENAR: the player is tapping the steps: move the highlight
                if q.kind == ORDER and screen.progress != st.get("progress", 0):
                    st["progress"] = screen.progress
                    self._emit(state="question_progress", progress=screen.progress)
                return
            if screen.answered and shown[1] == "game":
                return
        if screen.answered and screen.revealed is not None:
            learned = mem.save(q, screen.revealed) if mem else ""
            if mem:
                picked, ok = _picked(screen)
                mem.record_feedback(q, picked, ok)
                check = st.get("check")
                if check and check[0] == ident:        # how did the solver / AI do?
                    mine, who = check[1], check[2]
                    mem.log_check(q, mine, answers_match(mine, screen.revealed), who)
                    st["check"] = None
            st["ai_job"] = None                        # too late for the AI now
            st["shown"] = (ident, "game")
            self._emit(state="overlay_clear")
            self._emit(state="question", question=q, answer=screen.revealed, status="game",
                       learned=learned, verdict=screen.verdict, place=place)
        elif screen.answered:
            st["shown"] = (ident, "answered")
            self._emit(state="overlay_clear")
            self._emit(state="question", question=q, answer=None, status="answered",
                       verdict=screen.verdict, place=place)
        else:
            m = mem.lookup(q) if mem else None
            if m is not None:
                st["shown"] = (ident, "memory")
                st["ai_job"] = None
                self._emit(state="question", question=q, answer=m.answer, status="memory",
                           how=m.how, display=m.stored, place=place, progress=screen.progress)
            else:
                # never solve from a doubtful reading of numbers / signs
                sol = None if screen.text_unsure else solve_question(q)
                if sol is not None:
                    status = "solver" if sol.confidence >= 1.0 else "likely"
                    st["shown"] = (ident, status)
                    st["check"] = (ident, sol, "solver")
                    st["ai_job"] = None
                    self._emit(state="question", question=q, answer=sol, status=status,
                               place=place, progress=screen.progress)
                elif not (shown and shown[0] == ident and shown[1] in ("unknown", "thinking")):
                    self._ask_ai(q, ident, place, st)

    # -- AI (runs in the background; the screen keeps being watched) --------
    def _ask_ai(self, q, ident, place, st: dict) -> None:
        ai = self.ai
        ok, why = ai.status() if ai is not None else (False, "")
        if not ok or not q.options:
            st["shown"] = (ident, "unknown")
            self._emit(state="question", question=q, answer=None, status="unknown",
                       place=place, ai_note=why if self.cfg.ai_provider != "none" else "")
            return
        st["shown"] = (ident, "thinking")
        # timed express quiz: no slow internet search, one quick AI request
        timed = q.category == "PREGUNTAS EXPRESS"
        st["ai_job"] = (ident, self._ai_pool.submit(ai.answer, q, 6.0 if timed else 8.0,
                                                    not timed), q, place)
        self._emit(state="question", question=q, answer=None, status="thinking", place=place)

    def _poll_ai(self, st: dict) -> None:
        job = st.get("ai_job")
        if not job or not job[1].done():
            return
        st["ai_job"] = None
        ident, fut, q, place = job
        shown = st.get("shown")
        if not shown or shown[0] != ident or shown[1] != "thinking":
            return                              # the question changed or was answered
        try:
            res = fut.result()
        except Exception as exc:                # never let the AI stop the assistant
            res = None
            note = f"AI failed ({exc.__class__.__name__})"
        else:
            note = res.error
        ans = res.answer if res is not None else None
        if ans is None:
            st["shown"] = (ident, "unknown")
            self._emit(state="question", question=q, answer=None, status="unknown",
                       place=place, ai_note=note)
            return
        status = "ai" if ans.confidence >= 0.6 else "unsure"
        st["shown"] = (ident, status)
        st["check"] = (ident, ans, ans.source)
        self._emit(state="question", question=q, answer=ans, status=status, place=place,
                   progress=st.get("progress", 0), ai_info=f"{res.provider} · {res.ms / 1000:.1f} s"
                           + (f" · used {res.facts} learned facts" if res.facts else "")
                           + (" · searched the web" if res.used_search else ""))

    # -- money puzzle ------------------------------------------------------
    def _money_loop(self, grabber, analyzer, b: dict, panel: MoneyPanel) -> None:
        """Watch only the money puzzle's strip (about 40 times a second) until
        the puzzle closes. Every coin / bill is tracked and read while it
        crosses; the game's 'Vistos N/M' counter verifies the count."""
        tracker = MoneyTracker()
        ax, ay, aw, ah = panel.area()
        region = {"left": b["left"] + ax, "top": b["top"] + ay, "width": aw, "height": ah}

        def rel(r):
            return (r[0] - ax, r[1] - ay, r[2], r[3])

        def scr(r):
            return (b["left"] + r[0], b["top"] + r[1], r[2], r[3])

        sx, sy, sw, sh = rel(panel.strip)
        bx, by, bw, bh = rel(panel.band)
        place = {"input": scr(panel.input), "strip": scr(panel.strip)}
        kx, ky, kw, kh = panel.keypad
        pad = max(10, kh // 6)
        keypad_region = {"left": b["left"] + kx - pad, "top": b["top"] + ky - pad,
                         "width": kw + 2 * pad, "height": kh + 2 * pad}
        ocr_job = None
        next_ocr = next_check = 0.0
        missing_since: Optional[float] = None
        last_sig = None
        while not self._stop.is_set():
            t = time.monotonic()
            if self._paused.is_set():
                time.sleep(0.2)
                continue
            self._rescan.clear()
            img, _ = grabber.grab_bounds(region)
            tracker.update(img[sy:sy + sh, sx:sx + sw], t)
            if ocr_job is not None and ocr_job.done():
                try:
                    tracker.set_vistos(parse_vistos(ocr_job.result()))
                except Exception:
                    pass
                ocr_job = None
            if ocr_job is None and t >= next_ocr:
                band = img[by:by + bh, bx:bx + bw]
                tracker.set_legend(read_legend(band))
                left = prep_for_ocr(band[:, :int(bw * 0.45)], 3.0)
                ocr_job = analyzer.pool.submit(analyzer.ocr.text, left, 7)
                next_ocr = t + 0.3
            if t >= next_check:            # still open? (keypad still there)
                next_check = t + 0.2
                kimg, _ = grabber.grab_bounds(keypad_region)
                if find_grids(to_gray(kimg), min_n=9, max_n=12):
                    missing_since = None
                elif missing_since is None:
                    missing_since = t
                elif t - missing_since > 0.3:
                    return
            if self._snapshot.is_set():
                self._snapshot.clear()
                self._save_snapshot([img], None)
            st = tracker.state(t)
            sig = (st.total, st.counted, st.seen, st.expected, st.done, st.verified,
                   st.on_strip, tuple(st.items))
            if sig != last_sig:
                last_sig = sig
                self._emit(state="money", money=st, place=place)
            delay = 0.025 - (time.monotonic() - t)
            if delay > 0:
                self._stop.wait(delay)

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
