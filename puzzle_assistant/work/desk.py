"""The work helper's hands on Windows: screen pictures, clicks, chat typing and
"has the owner touched the mouse / keyboard?".

Clicking and typing reuse Go to Room's Windows input code and chat-box finding
(world_atlas/navigate.py) without changing it.
"""
from __future__ import annotations

import sys
import time
from typing import List, Optional, Tuple

import numpy as np

from .runner import Desk


def monitors() -> List[dict]:
    from ..capture import new_mss
    with new_mss() as sct:
        return [dict(m) for m in sct.monitors[1:]]


def grab_bounds(b: dict) -> np.ndarray:
    from ..capture import new_mss
    with new_mss() as sct:
        shot = sct.grab(b)
    img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return np.ascontiguousarray(img[:, :, :3])


class WindowsDesk(Desk):
    def __init__(self, bounds: dict):
        from ..world_atlas.navigate import _Win
        import ctypes
        from ctypes import wintypes
        self.b = dict(bounds)
        self.win = _Win()
        self._ct, self._wt = ctypes, wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        self._LII = LASTINPUTINFO

    def grab(self) -> Tuple[np.ndarray, dict]:
        return grab_bounds(self.b), self.b

    def click(self, x: int, y: int) -> None:
        self.win.click(int(x), int(y))

    def cursor(self):
        try:
            return self.win.cursor()
        except Exception:
            return None

    def stop_key(self) -> bool:
        """Esc held now, or pressed since the last check (0x8001)."""
        try:
            return bool(self._user32.GetAsyncKeyState(0x1B) & 0x8001)
        except Exception:
            return False

    def last_input(self) -> float:
        lii = self._LII()
        lii.cbSize = self._ct.sizeof(self._LII)
        if not self._user32.GetLastInputInfo(self._ct.byref(lii)):
            return -1e9
        now_tick = self._kernel32.GetTickCount() & 0xFFFFFFFF
        ago = ((now_tick - lii.dwTime) & 0xFFFFFFFF) / 1000.0
        return time.monotonic() - ago

    def type_in_chat(self, text: str) -> bool:
        """Same steps as Go to Room: find the chat box, click it, check it took
        the keyboard (gold border), type, Enter."""
        from ..world_atlas.navigate import chat_focused
        img = grab_bounds(self.b)
        found = find_chat_box(img)
        if found is None:
            return False
        icon, h = found
        sx, sy = self.b["left"] + icon[0] + 6 * h, self.b["top"] + icon[1]
        for _ in range(2):
            self.win.click(sx, sy)
            time.sleep(0.3)
            if chat_focused(grab_bounds(self.b), icon, h):
                break
        else:
            return False
        self.win.click(sx, sy)
        time.sleep(0.15)
        self.win.type_text(text)
        time.sleep(0.1)
        self.win.enter()
        return True

    def beep(self) -> None:
        try:
            import winsound
            winsound.MessageBeep(0x30)
        except Exception:
            pass


def find_chat_box(img: np.ndarray) -> Optional[Tuple[Tuple[int, int], int]]:
    """(chat icon centre, bar height) of the game's chat input.

    First as Go to Room does it (room-name bar, then the pink chat icon below);
    if the name bar can't be seen (e.g. SK's own window covers its left end -
    SK's window is invisible to screen pictures), the pink chat icon in the
    lower part of the screen with the dark input box to its right."""
    import cv2
    from ..world_atlas.navigate import find_chat_input
    from ..world_atlas.recognize import find_name_bar
    nb = find_name_bar(img)
    icon = find_chat_input(img, nb) if nb is not None else None
    if nb is not None and icon is not None:
        return icon, int(nb[3])
    H, W = img.shape[:2]
    y0 = int(H * 0.7)
    hsv = cv2.cvtColor(img[y0:], cv2.COLOR_BGR2HSV)
    pink = ((hsv[..., 0] > 140) & (hsv[..., 0] < 178) & (hsv[..., 1] > 70)
            & (hsv[..., 2] > 110)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(pink)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    found = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if not (6 <= w <= 30 and 6 <= h <= 30 and a >= 0.2 * w * h):
            continue
        cx, cy = x + w // 2, y0 + y + h // 2
        right = gray[max(0, cy - 3):cy + 4, cx + 3 * w:cx + 3 * w + 250]
        if right.size and right.mean() < 70 and right.std() < 25:   # the dark, empty input
            found.append(((cx, cy), 20))          # bar height as Go to Room measures it (~20 px)
    return found[0] if len(found) == 1 else None


def find_game_screen(ocr) -> Optional[dict]:
    """The monitor that shows the job panel."""
    import cv2
    from . import vision as V
    for m in monitors():
        img = grab_bounds(m)
        w = min(img.shape[1], max(400, img.shape[1] // 3))
        up = cv2.resize(img[:, :w], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        if V.find_panel(ocr.words(up, 11), (0, 0), 2.0) is not None:
            return m
    return None


def available() -> Tuple[bool, str]:
    if sys.platform != "win32":
        return False, "The work helper works on Windows only."
    return True, ""
