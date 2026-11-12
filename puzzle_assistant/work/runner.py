"""The work helper: does the job's walking tasks while the owner is present.

Owner-approved 2026-11-11 (DEVELOPMENT_NOTES.md, "Work automation"). It only
runs after the owner presses "Start working" in SK and stops by itself when
anything is unclear. Safeguards (all required by the owner's decision):

  * PRESENCE: at random times (every 8-15 min) SK asks "¿Sigues ahí?"; no
    answer within 45 s = stop.
  * STOP: Esc (anywhere) or "Stop working" stops it at once. Changed by the
    owner on 2026-11-12: "any input stops it" also stopped it when they had
    not touched anything (Windows registers SK's own clicks late). Moving
    the mouse no longer stops it: SK just doesn't click while the owner uses
    the mouse, and goes on once the pointer has been still for 2 s.
  * CHAT: when someone mentions the owner's Habbo name in the room chat, SK
    pauses and alerts; the owner continues with "Continue".
  * NOT SURE = STOP: a marker, button or panel step it can't read, or a click
    that doesn't lead to the next step, stops it with the reason shown.
  * TIME LIMIT: stops after the session length (default 60 min).
  * Only while the job panel is on screen; it never chooses a mini-game task
    (Montar un arma, Limpiar el banco) - those pause it for the owner.
  * ENERGY: types ":beber" in the chat when energy is below the set level
    (default 40), then checks it went up (no rise = stop).

How a task goes (learned from the owner's recordings):
  1. no task running: press "Ir" on a walking task (Colocar la munición,
     Barrer casquillos; the Recomendado one first);
  2. "Ve al objetivo": click the object under its teal "!" bubble ("Munición",
     "Basura") or the teal arrow, then the yellow button that opens
     ("Coger", "Recoger") - the character walks there and picks it up;
  3. "Llevas 1/1 ... Lleva la carga a su sitio": click the teal arrow's
     label/ring ("Armero", "Contenedor") - the character walks there;
  4. ammunition ends with counting the money ("Escribe el total"): SK's money
     counter must be sure (every coin/bill read, the game's "Vistos" count
     matches); then SK types the total on the game's keypad, checks the box
     shows it, and presses the tick.
"""
from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

from . import vision as V

STEP_TIMEOUT = 20.0      # s: a click must lead to the next step within this
BUTTON_WAIT = 2.0        # s: the yellow button must appear within this after clicking
PRESENCE_ANSWER = 45.0   # s to answer "¿Sigues ahí?"
SETTLE = 0.06            # s for Windows to register SK's own click before taking the baseline
HANDS_AWAY = 1.5         # s for the owner to take the hand off after a click in SK's window
MOUSE_SLACK = 12         # px: the pointer this far from where SK left it = the owner moved it
MOUSE_STILL = 2.0        # s the owner's pointer must rest before SK clicks again


# ---------------------------------------------------------------------------
# What the helper needs from the PC (Windows version below; tests use fakes)
# ---------------------------------------------------------------------------
class Desk:
    def grab(self) -> Tuple[np.ndarray, dict]:
        """(picture BGR, bounds {left, top, width, height}) of the game screen."""
        raise NotImplementedError

    def click(self, x: int, y: int) -> None:
        raise NotImplementedError

    def type_in_chat(self, text: str) -> bool:
        """Type `text` + Enter in the game chat; False if the chat box wasn't found."""
        raise NotImplementedError

    def last_input(self) -> float:
        """time.monotonic() of the last keyboard / mouse input (any source)."""
        return -1e9

    def stop_key(self) -> bool:
        """Esc is (or was just) pressed."""
        return False

    def cursor(self) -> Optional[Tuple[int, int]]:
        """Mouse pointer position (None = not known)."""
        return None

    def beep(self) -> None:
        pass


@dataclass
class Status:
    text: str
    kind: str = "info"           # info | ok | warn | stop


class WorkRunner(threading.Thread):
    def __init__(self, cfg, desk: Desk, ocr, status: Callable[[Status], None],
                 ask_presence: Callable[[], None], clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, rng: Optional[random.Random] = None):
        super().__init__(daemon=True, name="work-helper")
        self.cfg, self.desk, self.ocr = cfg, desk, ocr
        self._status_cb, self._ask_presence = status, ask_presence
        self.clock, self._sleep, self.rng = clock, sleep, rng or random.Random()
        self._stop_ev = threading.Event()
        self._presence_ok = threading.Event()
        self._continue = threading.Event()
        self.paused_reason: Optional[str] = None
        self.stopped_reason: Optional[str] = None
        self.money = None            # latest (MoneyState, place) from the engine
        self.money_at = 0.0
        self.money_seen_at = -1e9
        self._own_input_until = 0.0     # until then the owner may still be moving away
        self._input_base = -1e9          # Windows' last-input time right after SK's own input
        self._cursor_base: Optional[Tuple[int, int]] = None   # where SK left the pointer
        self._cursor_seen: Optional[Tuple[int, int]] = None
        self._owner_mouse_at = -1e9                      # when the owner last moved the pointer
        self._said_mouse = False
        self.energy = V.EnergyJudge(int(getattr(cfg, "work_energy_min", 40)))
        self._panel_box: Optional[V.Box] = None
        self._seen_chat: dict = {}
        self.tasks_done = 0
        self.started = 0.0
        self._doubt: dict = {}
        self._objetos: Optional[V.Point] = None      # the Objetos window's search box (cached)
        self._holding: Optional[str] = None          # what SK picked up for the running task
        self._holding_task: Optional[str] = None
        # SK's own windows on screen (left, top, width, height), kept up to date by
        # the UI. SK can't see them in its screen pictures (they are hidden from
        # capture), so a spot under one of them must never be clicked.
        self.sk_windows: List[Tuple[int, int, int, int]] = []

    # -- from the UI --------------------------------------------------------
    def stop(self, reason: str = "Stopped.") -> None:
        if self.stopped_reason is None:
            self.stopped_reason = reason
        self._stop_ev.set()
        self._continue.set()
        self._presence_ok.set()

    def presence_answered(self) -> None:
        self._own_input_until = self.clock() + HANDS_AWAY   # the click on the answer is the owner's
        self._presence_ok.set()

    def resume(self) -> None:
        self._own_input_until = self.clock() + HANDS_AWAY
        self._continue.set()

    def on_money(self, state, place) -> None:
        self.money_seen_at = self.clock()
        if state is not None and getattr(state, "done", False):
            self.money, self.money_at = (state, place), self.clock()

    # -- helpers ------------------------------------------------------------
    @property
    def stopping(self) -> bool:
        return self._stop_ev.is_set()

    def status(self, text: str, kind: str = "info") -> None:
        try:
            self._status_cb(Status(text, kind))
        except Exception:
            pass

    def wait(self, s: float) -> None:
        end = self.clock() + s
        while not self.stopping and self.clock() < end:
            self._check_hands_off()
            self._sleep(min(0.1, max(0.0, end - self.clock())))

    def _mark_own_input(self) -> None:
        """Right after SK's own click / typing: from now on any NEW input is
        the owner's (the mouse pointer must also stay where SK left it)."""
        self._sleep(SETTLE)
        self._input_base = self.desk.last_input()
        self._cursor_base = self.desk.cursor()

    def _check_hands_off(self) -> None:
        """Esc stops; the owner moving the pointer is noted (SK then waits
        before its next click - see _wait_mouse_free)."""
        if self.stopping:
            return
        if self.desk.stop_key():
            self.stop("Stopped: you pressed Esc.")
            return
        cur = self.desk.cursor()
        if cur is None:
            return
        if self.clock() < self._own_input_until:       # e.g. just answered "¿Sigues ahí?"
            self._cursor_base = self._cursor_seen = cur
            return
        if self._cursor_base is None:
            self._cursor_base = cur
        if max(abs(cur[0] - self._cursor_base[0]), abs(cur[1] - self._cursor_base[1])) > MOUSE_SLACK \
                and cur != self._cursor_seen:
            self._owner_mouse_at = self.clock()
        self._cursor_seen = cur

    def _wait_mouse_free(self) -> None:
        """Never click while the owner is using the mouse: wait until the
        pointer has rested MOUSE_STILL seconds."""
        self._check_hands_off()
        while not self.stopping and self.clock() - self._owner_mouse_at < MOUSE_STILL:
            if not self._said_mouse:
                self.status("You're using the mouse - SK waits until it rests (Esc stops).", "info")
                self._said_mouse = True
            self.wait(0.1)
        if self._said_mouse and not self.stopping:
            self._said_mouse = False
            self.status("Working again (Esc or “Stop working” stops it).", "ok")

    def _covered(self, sx: int, sy: int) -> bool:
        return any(x - 4 <= sx <= x + w + 4 and y - 4 <= sy <= y + h + 4
                   for x, y, w, h in list(self.sk_windows))

    def _screen_click(self, sx: int, sy: int) -> bool:
        """Click a screen point (after the owner's pointer has rested) unless
        one of SK's windows is there. False = not clicked (stopped)."""
        self._wait_mouse_free()
        if self.stopping:
            return False
        if self._covered(sx, sy):
            self.stop("Stopped: SK's window covers the spot to click - move SK's window "
                      "away from the game and start again.")
            return False
        self.desk.click(int(sx), int(sy))
        self._mark_own_input()
        return True

    def _click(self, pt: V.Point, b: dict) -> None:
        self._screen_click(b["left"] + int(pt[0]), b["top"] + int(pt[1]))

    # -- reading the screen ---------------------------------------------------
    def _ocr_line(self, img) -> str:
        return self.ocr.text(img, 7)

    def _ocr_digits(self, img) -> str:
        return self.ocr._run(img, 7, ["-c", "tessedit_char_whitelist=0123456789"], "txt")

    def read_panel(self, img) -> V.PanelState:
        import cv2
        for attempt in (0, 1):
            if self._panel_box is None:
                w = min(img.shape[1], max(400, img.shape[1] // 3))
                up = cv2.resize(img[:, :w], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                self._panel_box = V.find_panel(self.ocr.words(up, 11), (0, 0), 2.0)
                if self._panel_box is None:
                    return V.PanelState(False)
            x, y, w, h = self._panel_box
            crop = img[y:y + h, x:x + w]
            up = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            words = self.ocr.words(up, 11)
            for wd in words:
                wd.x, wd.y, wd.w, wd.h = wd.x // 2, wd.y // 2, wd.w // 2, wd.h // 2
            p = V.parse_panel(words, w, crop)
            if p.found:
                for o in p.offers:
                    if o.ir is not None:
                        o.ir = (o.ir[0] + x, o.ir[1] + y)
                return p
            self._panel_box = None                    # moved? look for it again
        return V.PanelState(False)

    def markers(self, img) -> List[V.Marker]:
        ms = V.find_markers(img)
        V.read_labels(img, ms, self._ocr_line)
        return ms

    # -- safeguards between steps ---------------------------------------------
    def _presence_due(self) -> float:
        lo = float(getattr(self.cfg, "work_presence_min", 8)) * 60
        hi = float(getattr(self.cfg, "work_presence_max", 15)) * 60
        return self.clock() + self.rng.uniform(lo, max(lo, hi))

    def _ask(self) -> bool:
        self._presence_ok.clear()
        self.status("¿Sigues ahí? Answer in SK's window to keep working.", "warn")
        self.desk.beep()
        self._ask_presence()
        end = self.clock() + PRESENCE_ANSWER
        while not self.stopping and self.clock() < end and not self._presence_ok.is_set():
            self._sleep(0.1)
        if self.stopping:
            return False
        if not self._presence_ok.is_set():
            self.stop("Stopped: no answer to “¿Sigues ahí?”.")
            return False
        self.status("Thanks - working again.", "ok")
        return True

    def _pause(self, reason: str) -> None:
        self.paused_reason = reason
        self._continue.clear()
        self.status(reason + " Press Continue when ready.", "warn")
        self.desk.beep()
        while not self.stopping and not self._continue.is_set():
            self._sleep(0.2)
        self.paused_reason = None
        self._own_input_until = max(self._own_input_until, self.clock() + HANDS_AWAY)

    def check_chat(self, img) -> Optional[str]:
        """A NEW chat line that mentions the owner's name (not their own line)."""
        import cv2
        name = V.norm(str(getattr(self.cfg, "work_my_name", "") or "")).strip()
        if not name:
            return None
        h, w = img.shape[:2]
        area = img[int(h * 0.04):int(h * 0.45), int(w * 0.12):int(w * 0.9)]
        up = cv2.resize(area, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        now = self.clock()
        self._seen_chat = {k: t for k, t in self._seen_chat.items() if now - t < 120}
        found = None
        for _, row in V.lines_of(self.ocr.words(up, 11), join_px=10):
            line = " ".join(wd.text for wd in row)
            key = re.sub(r"\W+", "", V.norm(line))
            if len(key) < 4:
                continue
            new = key not in self._seen_chat
            self._seen_chat[key] = now
            if not new:
                continue
            nl = V.norm(line)
            speaker = nl.split(":", 1)[0].strip() if ":" in nl else ""
            if name in nl and not speaker.endswith(name):
                found = line
        return found

    def drink_if_low(self, img) -> None:
        low, value = self.energy.judge(V.read_energy(img, self._ocr_digits))
        if not low:
            return
        self.status(f"Energy {value}: typing :beber", "info")
        self._wait_mouse_free()
        if self.stopping:
            return
        ok = self.desk.type_in_chat(":beber")
        self._mark_own_input()
        if not ok:
            self.stop("Stopped: energy is low and the chat box wasn't found to type :beber.")
            return
        self.wait(4.0)
        img2, _ = self.desk.grab()
        low2, value2 = self.energy.judge(V.read_energy(img2, self._ocr_digits))
        if value2 is None or value is None or value2 <= value:
            self.stop(f"Stopped: typed :beber but energy didn't go up ({value} → {value2}).")
        else:
            self.status(f"Energy back to {value2}.", "ok")

    # -- main loop ----------------------------------------------------------
    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:                       # never crash SK
            self.stop(f"Stopped: unexpected error ({type(exc).__name__}: {exc}).")
        finally:
            self.status(self.stopped_reason or "Stopped.", "stop")

    def _run(self) -> None:
        self.started = self.clock()
        limit = float(getattr(self.cfg, "work_session_minutes", 60)) * 60
        presence_at = self._presence_due()
        self._own_input_until = self.clock() + HANDS_AWAY   # the click on "Start working"
        self.desk.stop_key()                          # forget an Esc pressed before starting
        next_chat = next_energy = 0.0
        self.status("Working - press Esc or “Stop working” to stop.", "ok")
        while not self.stopping:
            now = self.clock()
            if now < self._own_input_until:              # the owner is taking the hand away:
                self.wait(self._own_input_until - now)   # SK does nothing meanwhile
                continue
            self._check_hands_off()
            if self.stopping:
                break
            if now - self.started >= limit:
                self.stop(f"Stopped: session time limit ({limit / 60:.0f} min) reached. "
                          f"{self.tasks_done} tasks done.")
                break
            if now >= presence_at:
                if not self._ask():
                    break
                presence_at = self._presence_due()
                continue
            img, b = self.desk.grab()
            if now >= next_chat:
                next_chat = now + 3.0
                hit = self.check_chat(img)
                if hit:
                    self._pause(f"Someone mentioned you in the chat: “{hit[:60]}”.")
                    continue
            if now >= next_energy:
                next_energy = now + 10.0
                self.drink_if_low(img)
                if self.stopping:
                    break
            self.step(img, b)
            self.wait(0.3)

    # -- one decision ---------------------------------------------------------
    CONFIRM = 3              # the same "can't tell" on this many fresh looks before stopping

    def _unsure(self, key: str, message: str, pause: bool = False) -> None:
        """Something is unclear. One picture can be caught mid-change (a window
        opening, the character walking in front of a label): look again, and
        only stop / pause when it stays unclear CONFIRM times in a row."""
        n = self._doubt.get(key, 0) + 1
        self._doubt = {key: n}
        if n < self.CONFIRM:
            return
        self._doubt = {}
        if pause:
            self._pause(message)
        else:
            self.stop(message)

    def _acted(self) -> None:
        self._doubt = {}

    def step(self, img, b) -> None:
        if self._money_ready():
            self.type_money(b)
            return
        p = self.read_panel(img)
        if not p.found:
            self._unsure("panel", "Stopped: the job panel isn't on screen.")
            return
        if p.working and not (p.going or p.carrying or p.deliver):
            # picking up / dropping takes a moment; a mini-game would stay
            self._wait_change(p, "the action at the spot", allow_money=True)
            return
        if p.active in V.MANUAL_TASKS:
            self._unsure("manual", f"“{p.active}” is a mini-game task - please do it yourself.", pause=True)
            return
        if not p.busy:
            self._holding = None                     # no task running: nothing in hand
        # Owner (2026-11-12): once the boxes are picked up ("Llevas 1/1 cajas")
        # SK must go on to the rack, never back to the boxes - even when that
        # line isn't read: step 2 or later, "Lleva la carga", or SK's own memory
        # of the pick-up all mean "take it there".
        holding = self._holding is not None and (p.active is None or self._holding_task is None
                                                 or p.active == self._holding_task)
        later_step = p.step is not None and p.step[0] >= 2
        if p.load is not None and p.load[0] < p.load[1]:
            self.pick_up(img, b, p)                  # "Llevas 1/3": collect the rest first
        elif p.carrying or p.deliver or later_step:
            self.deliver(img, b, p)
        elif holding and not p.going:
            if self._holding == "Basura" and self._basura_visible(img):
                self.pick_up(img, b, p)              # more trash on the floor: collect it first
            else:
                self.deliver(img, b, p)
        elif p.going or p.active:
            self.pick_up(img, b, p)
        else:
            self.choose_task(b, p)

    def _picked(self, what: Optional[str], p: V.PanelState) -> None:
        """Remember that something was picked up for the running task."""
        self._holding = what or "?"
        self._holding_task = p.active

    def _basura_visible(self, img) -> bool:
        return any(m.kind == "bubble" and m.label == "Basura" for m in self.markers(img))

    def choose_task(self, b, p: V.PanelState) -> None:
        walking = [o for o in p.offers if o.task in V.WALKING_TASKS]
        if not walking:
            if p.offers:
                self._unsure("offers", "Only mini-game tasks are offered right now.", pause=True)
            else:
                self._unsure("offers", "Stopped: no tasks found in the job panel.")
            return
        o = sorted(walking, key=lambda o: not o.recommended)[0]
        if o.ir is None:
            self._unsure("ir", f"Stopped: can't find the “Ir” button of “{o.task}”.")
            return
        self._acted()
        self._holding = None
        self.status(f"Starting “{o.task}”.")
        self._click(o.ir, b)
        self._wait_change(p, f"“{o.task}” to start")

    def _target_label(self, p: V.PanelState) -> Optional[str]:
        if p.load_kind:                              # "Llevas 1/3 de basura" says what is collected
            return p.load_kind
        return V.WALKING_TASKS.get(p.active or "")

    def pick_up(self, img, b, p: V.PanelState) -> None:
        """Pick up the next thing. Owner (2026-11-12): what to pick up is always
        under a "!" bubble with the task's label - for Barrer casquillos there
        can be several "Basura" and ALL of them are collected before the load
        goes to the bin. So the labelled bubbles come first (any one will do;
        one that gives no button is skipped), then the task's arrow, then -
        for bullets the game doesn't show - the Objetos list."""
        want = self._target_label(p)
        ms = self.markers(img)
        arrows = [m for m in ms if m.kind == "arrow"]
        # owner (2026-11-12): the thing's "!" bubble can sit right on top of an
        # arrow, hiding its own label - then the arrow carries the name
        cands = sorted((m for m in ms if want and m.label == want),
                       key=lambda m: (m.kind != "bubble", m.box[1], m.box[0]))
        if len(arrows) > 1 and not cands:
            self._unsure("target", f"Stopped: {len(arrows)} arrows on screen - not sure which one.")
            return
        targets = cands + [a for a in arrows if len(arrows) == 1 and not p.carrying and a not in cands]
        if not targets:
            if want == "Basura" and self.objetos_bullet(img, b, p):
                return                               # an invisible bullet, from the Objetos list
            self._unsure("target", f"Stopped: can't find the “{want or 'target'}” marker.")
            return
        self._acted()
        for target in targets:
            btn = self._try_target(target, b)
            if self.stopping:
                return
            if btn is not None:
                self._click(V.button_click_point(btn), b)
                self._picked(want, p)
                self._wait_change(p, "the pick-up")
                return
        if want == "Basura":
            # the marked bullets may be ones the game doesn't show (glitch)
            img3, _ = self.desk.grab()
            if self.objetos_bullet(img3, b, p):
                return
        if not self.stopping:
            self.stop("Stopped: no yellow button appeared for any marked object.")

    def _try_target(self, target: V.Marker, b) -> Optional[V.Box]:
        """Click the object under a marker (then its label once); the yellow
        button that opened, or None."""
        img, _ = self.desk.grab()
        before = V.yellow_buttons(img)
        pt = target.click_point
        self.status(f"Picking up: {target.label or 'marked object'}.")
        self._click(pt, b)
        btn = self._wait_button(before, pt)
        if btn is None and target.label_box is not None and not self.stopping:
            x, y, w, h = target.label_box
            pt = (x + w // 2, y + h // 2)
            img2, _ = self.desk.grab()
            before = V.yellow_buttons(img2)
            self._click(pt, b)
            btn = self._wait_button(before, pt)
        return btn

    def objetos_bullet(self, img, b, p: V.PanelState) -> bool:
        """Owner, 2026-11-12: some bullets can't be seen (a game glitch). The
        owner keeps the game's "Objetos" window open with "bala" typed in its
        search box; SK clicks the first "Bala" of the LIST under the box (never
        the typed one), then the yellow button that appears. False = no
        Objetos window / no bullet in its list (nothing clicked)."""
        words = lambda im, psm: self.ocr.words(im, psm)
        if self._objetos is None:
            self._objetos = V.find_objetos_search(img, self.ocr.words(img, 11), words)
            if self._objetos is None:
                return False
        entries = V.objetos_entries(img, self._objetos, words)
        if not entries:
            self._objetos = None                     # moved or closed? look again next time
            return False
        self._acted()
        pt = entries[0]
        before = V.yellow_buttons(img)
        self.status("Invisible bullet: clicking the first “Bala” in the Objetos list.")
        self._click(pt, b)
        btn = self._wait_button(before, pt, within=4000)     # its button can be anywhere
        if self.stopping:
            return True
        if btn is None:
            self.stop("Stopped: clicked “Bala” in the Objetos list but no yellow button appeared.")
            return True
        self._click(V.button_click_point(btn), b)
        self._picked("Basura", p)
        self._wait_change(p, "the pick-up from the Objetos list")
        return True

    def _wait_button(self, before, pt, within: int = 320) -> Optional[V.Box]:
        end = self.clock() + BUTTON_WAIT
        while not self.stopping and self.clock() < end:
            self.wait(0.25)
            img, _ = self.desk.grab()
            btn = V.new_button_near(before, V.yellow_buttons(img), pt, within)
            if btn is not None:
                return btn
        return None

    def deliver(self, img, b, p: V.PanelState) -> None:
        arrows = [m for m in self.markers(img) if m.kind == "arrow"]
        if len(arrows) != 1:
            self._unsure("deliver", "Stopped: can't tell where to take the load "
                         f"({len(arrows)} arrows on screen).")
            return
        self._acted()
        a = arrows[0]
        if self._is_ammo(a, p):
            self.place_ammo(a, b, p)
            return
        self.status(f"Taking it to {a.label or 'the marked spot'}.")
        pt = self.deliver_point(a, p)
        before = V.yellow_buttons(img)
        self._click(pt, b)
        btn = self._wait_button(before, pt)          # some spots open a button first
        if btn is not None:
            self._click(V.button_click_point(btn), b)
        self._wait_change(p, "the delivery", allow_money=True)

    @staticmethod
    def _is_ammo(a: V.Marker, p: V.PanelState) -> bool:
        return p.load_kind == "Munición" or p.active == "Colocar la munición" or a.label == "Armero"

    # spots to try on the gun rack, around the arrow's centre (px): the desk is
    # right under the rack, so a click can select the desk instead
    RACK_TRIES = ((0, 0), (0, -6), (-7, -3), (7, -3), (0, -11))

    def place_ammo(self, a: V.Marker, b, p: V.PanelState) -> None:
        """Owner (2026-11-12): click the "Armero para Pistolas" (the three guns,
        where the arrow is) and check the window that opens is really it -
        not the desk under it - before pressing its "Colocar"."""
        x, y, w, h = a.box
        cx, cy = x + w // 2, y + h // 2
        seen = []
        words = lambda im, psm: self.ocr.words(im, psm)
        for dx, dy in self.RACK_TRIES:
            pt = (cx + dx, cy + dy)
            self.status("Taking the ammunition to the Armero para Pistolas.")
            self._click(pt, b)
            end = self.clock() + BUTTON_WAIT
            found = None
            while found is None and not self.stopping and self.clock() < end:
                self.wait(0.25)
                img2, _ = self.desk.grab()
                # every button is checked by its name: after a wrong pick (the
                # desk) its window can stay open where the rack's then appears
                for btn in V.yellow_buttons(img2):
                    name = V.selected_name(img2, btn, words)
                    if V.is_gun_rack(name):
                        found = btn
                        break
                    if name and name not in seen:
                        seen.append(name)
            if self.stopping:
                return
            if found is not None:
                self._click(V.button_click_point(found), b)          # "Colocar"
                self._wait_change(p, "placing the ammunition", allow_money=True)
                return
        self.stop("Stopped: couldn't select the Armero para Pistolas"
                  + (f" (selected: {', '.join(seen)})" if seen else "") + ".")

    @staticmethod
    def deliver_point(a: V.Marker, p: V.PanelState) -> V.Point:
        """Where to click to drop the load. Owner (2026-11-12) and the recording:
        the ammunition goes on the three guns on the counter - right where the
        arrow is (above its "Armero" label); the trash goes to the bin, whose
        "Contenedor" label was clicked in the recordings."""
        ammo = p.load_kind == "Munición" or p.active == "Colocar la munición" or a.label == "Armero"
        if ammo:
            x, y, w, h = a.box
            return x + w // 2, y + h // 2
        return a.click_point

    def _wait_change(self, p: V.PanelState, what: str, allow_money: bool = False) -> None:
        """Wait until the panel shows something else (the next step)."""
        sig = (p.active, p.step, p.load, p.going, p.carrying, p.deliver, p.working, p.remaining,
               tuple(o.title for o in p.offers))
        end = self.clock() + STEP_TIMEOUT
        while not self.stopping and self.clock() < end:
            self.wait(0.6)
            if allow_money and self._money_ready():
                return
            if self.clock() - self.money_seen_at < 2.0:     # the money window is running
                end = max(end, self.clock() + 5.0)
            img, _ = self.desk.grab()
            q = self.read_panel(img)
            if not q.found:
                continue                                # e.g. covered by the money window
            qs = (q.active, q.step, q.load, q.going, q.carrying, q.deliver, q.working, q.remaining,
                  tuple(o.title for o in q.offers))
            if qs != sig:
                if p.active and not q.active and (q.remaining or 0) < (p.remaining or 10 ** 6):
                    self._holding = None
                    self.tasks_done += 1
                    self.status(f"Task done ({self.tasks_done} this session).", "ok")
                return
        if not self.stopping:
            self.stop(f"Stopped: nothing happened after {what} (waited {STEP_TIMEOUT:.0f} s).")

    # -- the money count --------------------------------------------------------
    def _money_ready(self) -> bool:
        if self.money is None:
            return False
        st, place = self.money
        return st is not None and bool(st.done) and self.clock() - self.money_at < 30.0 \
            and "keypad" in place

    def type_money(self, b) -> None:
        st, place = self.money
        self.money = None
        if not st.verified or not all(s for _, s in st.items) or st.problems:
            self._pause("Money count not sure (" + ("; ".join(st.problems) or "unclear item")
                        + ") - please type the total yourself.")
            return
        keys = keypad_points(place["keypad"])
        if keys is None:
            self.stop("Stopped: couldn't work out the game's keypad.")
            return
        total = str(int(st.total))
        self.status(f"Typing the total: {total}.")
        for d in total:
            sx, sy = keys[d]
            if not self._screen_click(sx, sy):                # screen coordinates already
                return
            self.wait(0.25)
            if self.stopping:
                return
        img, bb = self.desk.grab()
        shown = read_input_digits(img, bb, place["input"], self._ocr_digits)
        if shown != total:
            sx, sy = keys["del"]
            for _ in total:
                if not self._screen_click(sx, sy):
                    return
                self.wait(0.15)
            self._pause(f"Typed {total} but the box shows “{shown or '?'}” - "
                        "cleared it; please type the total yourself.")
            return
        sx, sy = keys["ok"]
        if not self._screen_click(sx, sy):
            return
        self.status(f"Total {total} sent.", "ok")
        self.wait(1.5)


def keypad_points(k) -> Optional[dict]:
    """Screen centres of the keypad buttons 1-9 / del 0 ok (3 x 4 grid)."""
    x, y, w, h = k
    if w <= 0 or not 0.33 <= h / w <= 0.6:          # must be the full 4-row keypad
        return None
    cw, rh = w / 3, h / 4
    order = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "del", "0", "ok"]
    return {key: (x + (i % 3 + 0.5) * cw, y + (i // 3 + 0.5) * rh) for i, key in enumerate(order)}


def read_input_digits(img, b, inp, ocr_digits) -> str:
    """Digits shown in the money window's answer box (inp in screen px)."""
    import cv2
    x, y, w, h = inp
    x, y = x - b["left"], y - b["top"]
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return ""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, t = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (t > 0).mean() < 0.5:                         # mostly dark = light digits on dark: invert
        t = 255 - t
    t = cv2.copyMakeBorder(t, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return re.sub(r"\D", "", ocr_digits(t) or "")
