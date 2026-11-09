"""Go to Room (World Atlas stage 2): one click of the owner = one taxi trip.

Agreed with the owner: the hotel allows helpers that are not fully
autonomous (it has its own macro). SK types `:taxi <room ID>` in the hotel
chat ONLY when the owner clicks "Go to Room", one command per click, never
on its own. Everything else in SK stays read-only.

Steps (each must succeed, otherwise nothing is typed):
  1. the game's room view is visible (name bar found) and no trip is running;
     at least 3.5 s since the last trip (the hotel's taxi cooldown is ~3 s);
  2. the chat input is found right below the name bar (pink chat icon);
  3. SK clicks into the chat input once and checks it is focused (its
     border turns gold) - if not, it stops without typing;
  4. it types ":taxi <id>" and presses Enter, then puts the mouse back;
  5. it waits up to 10 s for the room change and checks the destination
     (ID from the share panel, or the room name) - only then is the trip
     reported as done. A different or no room change is reported as failed.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

from .recognize import chat_icon_below, find_name_bar

COOLDOWN = 3.5
Box = Tuple[int, int, int, int]


# ---------------------------------------------------------------------------
# Finding the chat input on the screen picture (pure functions, testable)
# ---------------------------------------------------------------------------
def find_chat_input(img: np.ndarray, name_box: Optional[Box] = None) -> Optional[Tuple[int, int]]:
    """Centre of the chat icon inside the chat input (pink speech bubble),
    just below the room-name bar. Returns (x, y) in image pixels."""
    nb = name_box or find_name_bar(img)
    if nb is None:
        return None
    return chat_icon_below(img, nb)


def chat_focused(img: np.ndarray, icon: Tuple[int, int], h: int = 20) -> bool:
    """True when the chat input's border is gold (= it has the keyboard)."""
    ix, iy = icon
    x0, x1 = ix + 2 * h, min(img.shape[1], ix + 14 * h)
    rows = img[max(0, iy - int(1.4 * h)):iy + int(1.4 * h), x0:x1].astype(np.int16)
    if rows.size == 0:
        return False
    warm = (rows[..., 2] - rows[..., 0]).mean(axis=1)      # red minus blue, per row
    return float(warm.max()) >= 12.0


# ---------------------------------------------------------------------------
# Windows input (only used by Navigator.go, i.e. after the owner's click)
# ---------------------------------------------------------------------------
class _Win:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("pad", ctypes.c_byte * 32)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.KEYBDINPUT, self.MOUSEINPUT, self.INPUT = KEYBDINPUT, MOUSEINPUT, INPUT

    def _send(self, *inputs) -> None:
        arr = (self.INPUT * len(inputs))(*inputs)
        self.user32.SendInput(len(inputs), arr, self.ct.sizeof(self.INPUT))

    def cursor(self) -> Tuple[int, int]:
        p = self.wt.POINT()
        self.user32.GetCursorPos(self.ct.byref(p))
        return p.x, p.y

    def click(self, x: int, y: int) -> None:
        self.user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        down = self.INPUT(type=0, u=self._u_mouse(0x0002))
        up = self.INPUT(type=0, u=self._u_mouse(0x0004))
        self._send(down, up)

    def _u_mouse(self, flags):
        u = self.INPUT._fields_[1][1]()
        u.mi = self.MOUSEINPUT(0, 0, 0, flags, 0, None)
        return u

    def type_text(self, text: str) -> None:
        for ch in text:
            for flags in (0x0004, 0x0004 | 0x0002):          # KEYEVENTF_UNICODE (+ KEYUP)
                u = self.INPUT._fields_[1][1]()
                u.ki = self.KEYBDINPUT(0, ord(ch), flags, 0, None)
                self._send(self.INPUT(type=1, u=u))
            time.sleep(0.01)

    def enter(self) -> None:
        for flags in (0, 0x0002):
            u = self.INPUT._fields_[1][1]()
            u.ki = self.KEYBDINPUT(0x0D, 0, flags, 0, None)
            self._send(self.INPUT(type=1, u=u))

    def move(self, x: int, y: int) -> None:
        self.user32.SetCursorPos(int(x), int(y))


# ---------------------------------------------------------------------------
class Navigator:
    """Runs one taxi trip per call of go(); reports progress via `status`."""

    def __init__(self, explorer, atlas):
        self.explorer, self.atlas = explorer, atlas
        self.busy = False
        self.last_trip = -1e9
        self.message = ""
        self._lock = threading.Lock()

    def available(self) -> Tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Go to Room works on Windows only."
        if self.busy:
            return False, "A trip is already running."
        wait = COOLDOWN - (time.monotonic() - self.last_trip)
        if wait > 0:
            return False, f"Wait {wait:.0f} s (taxi cooldown)."
        v = getattr(self.explorer, "view", None)
        if v is None or not v.in_room or getattr(self.explorer, "screen", None) is None:
            return False, "SK can't see the game's room view right now."
        return True, ""

    def go(self, room_id: int, status: Callable[[str, str], None]) -> None:
        """status(text, kind) with kind 'info' | 'ok' | 'fail'. Starts a thread."""
        ok, why = self.available()
        if not ok:
            status(why, "fail")
            return
        with self._lock:
            if self.busy:
                return
            self.busy = True
        threading.Thread(target=self._trip, args=(room_id, status), daemon=True).start()

    def _trip(self, room_id: int, status) -> None:
        try:
            self._trip_inner(room_id, status)
        except Exception as exc:                      # never crash SK
            status(f"Go to Room stopped: {exc}", "fail")
        finally:
            self.last_trip = time.monotonic()
            self.busy = False

    def _grab(self):
        return self._grab_at(self.explorer.screen)

    def _grab_at(self, b):
        from ..capture import new_mss
        with new_mss() as sct:
            shot = sct.grab(b)
        img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(img[:, :, :3]), b

    def _find_chat(self):
        """(bounds, name bar, chat icon, same_screen) for the screen that shows the
        game's chat input: the explorer's screen first, then every other monitor
        (v3.1 - the game may be on the other one). same_screen: it is the
        explorer's screen (then the focus check reads it the same way)."""
        img, b = self._grab()
        nb = find_name_bar(img)
        icon = find_chat_input(img, nb) if nb is not None else None
        if nb is not None and icon is not None:
            return b, nb, icon, True
        try:
            from ..capture import new_mss
            with new_mss() as sct:
                others = [dict(m) for m in sct.monitors[1:]]
        except Exception:
            others = []
        for m in others:
            if m == b:
                continue
            img, mb = self._grab_at(m)
            nb = find_name_bar(img)
            icon = find_chat_input(img, nb) if nb is not None else None
            if nb is not None and icon is not None:
                return mb, nb, icon, False
        return None, None, None, False

    def _trip_inner(self, room_id: int, status) -> None:
        target = self.atlas.room(room_id)
        tname = target["name"] if target else f"room {room_id}"
        start = self.explorer.tracker.current
        if start and start.get("room_id") == room_id:
            status(f"You are already in {tname}.", "ok")
            return
        status(f"Going to {tname} ({room_id})…", "info")
        b, nb, icon, same = self._find_chat()
        if nb is None or icon is None:
            status("Chat box not found on any screen - nothing was typed.", "fail")
            return
        win = _Win()
        back = win.cursor()
        sx, sy = b["left"] + icon[0] + 6 * nb[3], b["top"] + icon[1]
        try:
            win.click(sx, sy)                          # put the keyboard in the chat box
            time.sleep(0.25)
            img2, _ = self._grab() if same else self._grab_at(b)
            if not chat_focused(img2, icon, nb[3]):
                status("The chat box didn't take the keyboard - nothing was typed.", "fail")
                return
            win.type_text(f":taxi {room_id}")
            win.enter()
        finally:
            win.move(*back)
        status(f"Sent “:taxi {room_id}”. Waiting for {tname}…", "info")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10.0:
            time.sleep(0.4)
            cur = self.explorer.tracker.current
            if cur and cur is not start and cur.get("room_id") == room_id:
                status(f"Arrived: {tname} ({room_id}) ✓", "ok")
                return
            if cur and cur is not start and cur.get("room_id") not in (None, room_id):
                status(f"Arrived somewhere else: {cur['name']} ({cur['room_id']}). "
                       "Not counted as this room.", "fail")
                return
        status(f"No arrival at {tname} seen. The taxi may have refused (walk-only place, "
               "passive mode, place not found) - nothing was recorded.", "fail")
