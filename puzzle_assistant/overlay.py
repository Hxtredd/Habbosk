"""Click-through overlay that draws the answer directly on the puzzle cards.

    (1)(2)(3)...   step number on each piece to mount, the next one highlighted
    ✓              piece already mounted
    ✕              other caliber: don't click
    ?              card not read clearly (no numbers are shown then)
    PROBAR         when every piece is mounted

The overlay is a transparent, always-on-top window that ignores the mouse:
every click goes straight through to the game. It never clicks, moves the
mouse or types anything itself. It is also hidden from screen capture, so it
can never disturb the reading.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .analyzer import Analysis

Rect = Tuple[int, int, int, int]


@dataclass
class Item:
    kind: str          # "next" | "step" | "done" | "skip" | "doubt" | "probar"
    x: int             # centre, screen pixels
    y: int
    r: int             # radius (for "probar": half height)
    text: str


def badge_geometry(card: Rect) -> Tuple[int, int, int]:
    """Where the step badge sits on a card: over the letter badge (left side),
    clear of the card outline (used to see the green 'mounted' border)."""
    x, y, w, h = card
    r = max(8, int(0.32 * h))
    cx = x + max(int(0.17 * w), r + 6)
    cy = y + h // 2
    return cx, cy, r


def badge_mask_rect(card_w: int, card_h: int) -> Rect:
    """The same area in card-local coordinates (ignored when fingerprinting)."""
    cx, cy, r = badge_geometry((0, 0, card_w, card_h))
    return (max(0, cx - r - 2), max(0, cy - r - 2), 2 * r + 5, 2 * r + 5)


def step_numbers(a: Analysis) -> Dict[str, int]:
    s = a.solution
    if s is None:
        return {}
    numbering = getattr(s, "numbering", None)
    if numbering:
        return dict(numbering)
    return {l: i + 1 for i, l in enumerate(s.order)}


def overlay_items(a: Optional[Analysis], cards: Sequence[Rect]) -> List[Item]:
    """Pure layout: what to draw where (screen coordinates)."""
    if a is None or a.solution is None or a.puzzle is None or not cards:
        return []
    s, p = a.solution, a.puzzle
    letters = sorted(p.pieces)
    by_letter = {l: c for l, c in zip(letters, cards)}
    items: List[Item] = []

    if not (s.ok and s.certain):
        for l in s.doubtful:
            if l in by_letter:
                cx, cy, r = badge_geometry(by_letter[l])
                items.append(Item("doubt", cx, cy, r, "?"))
        return items

    nums = step_numbers(a)
    nxt = s.remaining[0] if s.remaining else None
    for l, card in by_letter.items():
        cx, cy, r = badge_geometry(card)
        if l in s.excluded:
            items.append(Item("skip", cx, cy, int(r * 0.8), "✕"))
        elif l in s.done:
            items.append(Item("done", cx, cy, int(r * 0.8), "✓"))
        elif l in nums:
            items.append(Item("next" if l == nxt else "step", cx, cy, r, str(nums[l])))
    if not s.remaining:
        right = max(c[0] + c[2] for c in cards)
        bottom = max(c[1] + c[3] for c in cards)
        cw = sorted(c[2] for c in cards)[len(cards) // 2]
        ch = sorted(c[3] for c in cards)[len(cards) // 2]
        items.append(Item("probar", right - int(0.45 * cw), bottom + int(0.42 * ch),
                          max(10, int(0.3 * ch)), "PROBAR"))
    return items


def money_overlay_items(st, place: Optional[dict]) -> List[Item]:
    """The money total as a badge on the left of the input box (the typed
    digits appear on the right)."""
    if st is None or not place or "input" not in place:
        return []
    x, y, w, h = place["input"]
    if st.done and st.verified:
        kind, text = "total_ok", f"= {st.total}"
    elif st.done:
        kind, text = "total_unsure", f"? {st.total}"
    elif st.counted:
        kind, text = "total_count", f"{st.total} …"
    else:
        return []
    r = max(9, int(h * 0.42))
    return [Item(kind, x + int(w * 0.2), y + h // 2, r, text)]


def question_overlay_items(q, ans, status: str, place: Optional[dict]) -> List[Item]:
    """A known answer drawn on the question window: a green check on the
    correct option, step numbers for ORDENAR, matching numbers for PAREJAS.
    Nothing is drawn for unknown questions or once the game shows the result."""
    if ans is None or not place or status not in ("memory", "solver"):
        return []
    boxes = place["boxes"]
    nl = place.get("n_left", 0)
    items: List[Item] = []

    def at(box, kind, text):
        x, y, w, h = box
        r = max(9, int(h * 0.36))
        items.append(Item(kind, x + w - r - 8, y + h // 2, r, text))

    if q.kind == "choice":
        if ans.choice in q.options:
            at(boxes[q.options.index(ans.choice)], "answer", "")
    elif q.kind == "order":
        for i, opt in enumerate(ans.order):
            if opt in q.options:
                at(boxes[q.options.index(opt)], "next" if i == 0 else "step", str(i + 1))
    elif q.kind == "pairs" and nl:
        for i, (a, b) in enumerate(ans.pairs.items()):
            if a in q.options and b in q.right:
                at(boxes[q.options.index(a)], "step", str(i + 1))
                at(boxes[nl + q.right.index(b)], "step", str(i + 1))
    return items


# ---------------------------------------------------------------------------
# Windows layered window (per-pixel alpha, positioned in screen pixels)
# ---------------------------------------------------------------------------
class Overlay:
    """Transparent, click-through, capture-excluded layer for the badges.

    The badges are rendered into one RGBA image and handed to Windows with
    UpdateLayeredWindow, together with the exact screen position of that
    image. Windows places it there directly (any monitor, any scaling), so
    the badges land on the cards whatever the screen layout is.
    """

    def __init__(self, root):
        self.available = sys.platform == "win32"
        self.hidden_from_capture = False
        self.error = ""
        self.win = None
        self.hwnd = None
        if not self.available:
            return
        try:
            import tkinter as tk
            self.win = tk.Toplevel(root)
            self.win.overrideredirect(True)
            self.win.geometry("1x1+0+0")
            self.win.update_idletasks()
            self._api = _Win32()
            self.hwnd = self._api.toplevel_hwnd(self.win)
            self.hidden_from_capture = self._api.make_overlay_window(self.hwnd)
            self.clear()
        except Exception as exc:  # never let the overlay break the app
            self.available = False
            self.error = f"{type(exc).__name__}: {exc}"

    def clear(self) -> None:
        if self.hwnd:
            self._api.blit(self.hwnd, None, 0, 0)

    def show(self, items: List[Item]) -> None:
        if not self.hwnd:
            return
        from .render import compose_overlay
        img, x, y = compose_overlay(items)
        self._api.blit(self.hwnd, img, x, y)


class _Win32:
    """Minimal ctypes bindings (64-bit safe: every handle is a c_void_p)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        self.ct, self.wt = ctypes, wt
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u, self.g = u, g
        H = ctypes.c_void_p
        u.GetParent.restype, u.GetParent.argtypes = H, [H]
        u.GetDC.restype, u.GetDC.argtypes = H, [H]
        u.ReleaseDC.argtypes = [H, H]
        # 32-bit Windows has no *LongPtr* exports (they are macros there)
        self.get_long = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        self.set_long = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        self.get_long.restype = ctypes.c_ssize_t
        self.get_long.argtypes = [H, ctypes.c_int]
        self.set_long.restype = ctypes.c_ssize_t
        self.set_long.argtypes = [H, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowDisplayAffinity.argtypes = [H, wt.DWORD]
        u.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
        u.ShowWindow.argtypes = [H, ctypes.c_int]
        u.UpdateLayeredWindow.argtypes = [H, H, ctypes.c_void_p, ctypes.c_void_p, H,
                                          ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = H, [H]
        g.CreateDIBSection.restype = H
        g.CreateDIBSection.argtypes = [H, ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p), H, wt.DWORD]
        g.SelectObject.restype, g.SelectObject.argtypes = H, [H, H]
        g.DeleteObject.argtypes = [H]
        g.DeleteDC.argtypes = [H]

        class BLEND(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BMI(ctypes.Structure):
            _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]

        self.BLEND, self.BIH, self.BMI = BLEND, BIH, BMI

    def toplevel_hwnd(self, win):
        try:  # Tk's own handle of the outer window
            h = int(win.wm_frame(), 16)
            if h:
                return h
        except Exception:
            pass
        return self.u.GetParent(win.winfo_id()) or win.winfo_id()

    def make_overlay_window(self, hwnd) -> bool:
        GWL_EXSTYLE = -20
        style = self.get_long(hwnd, GWL_EXSTYLE)
        style |= (0x00080000 | 0x00000020 | 0x00000080 | 0x08000000 | 0x00000008)
        #         LAYERED      TRANSPARENT  TOOLWINDOW   NOACTIVATE   TOPMOST
        self.set_long(hwnd, GWL_EXSTYLE, style)
        HWND_TOPMOST = self.ct.c_void_p(-1)
        # SWP_NOMOVE|NOSIZE|NOACTIVATE|FRAMECHANGED|SHOWWINDOW
        self.u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010 | 0x0020 | 0x0040)
        try:  # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+)
            return bool(self.u.SetWindowDisplayAffinity(hwnd, 0x11))
        except Exception:
            return False

    def blit(self, hwnd, img, x: int, y: int) -> None:
        """Show `img` (PIL RGBA) with its top-left at screen pixel (x, y)."""
        import numpy as np
        ct, wt = self.ct, self.wt
        if img is None:
            arr = np.zeros((1, 1, 4), np.uint8)
        else:
            a = np.asarray(img.convert("RGBA"), dtype=np.uint16)
            alpha = a[..., 3:4]
            rgb = (a[..., :3] * alpha + 127) // 255      # premultiplied alpha
            arr = np.empty(a.shape, np.uint8)
            arr[..., 0] = rgb[..., 2]                     # BGRA
            arr[..., 1] = rgb[..., 1]
            arr[..., 2] = rgb[..., 0]
            arr[..., 3] = a[..., 3]
            arr = np.ascontiguousarray(arr)
        h, w = arr.shape[:2]
        screen = self.u.GetDC(None)
        mem = self.g.CreateCompatibleDC(screen)
        bmi = self.BMI()
        bmi.bmiHeader.biSize = ct.sizeof(self.BIH)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h   # top-down
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ct.c_void_p()
        hbmp = self.g.CreateDIBSection(screen, ct.byref(bmi), 0, ct.byref(bits), None, 0)
        old = self.g.SelectObject(mem, hbmp)
        try:
            ct.memmove(bits, arr.ctypes.data, w * h * 4)
            dst, size, src = wt.POINT(int(x), int(y)), wt.SIZE(w, h), wt.POINT(0, 0)
            blend = self.BLEND(0, 0, 255, 1)              # AC_SRC_OVER, AC_SRC_ALPHA
            self.u.UpdateLayeredWindow(hwnd, screen, ct.byref(dst), ct.byref(size), mem,
                                       ct.byref(src), 0, ct.byref(blend), 2)  # ULW_ALPHA
            self.u.ShowWindow(hwnd, 4)                    # SW_SHOWNOACTIVATE
        finally:
            self.g.SelectObject(mem, old)
            self.g.DeleteObject(hbmp)
            self.g.DeleteDC(mem)
            self.u.ReleaseDC(None, screen)
