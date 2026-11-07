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


# ---------------------------------------------------------------------------
# Tk window (Windows only: needs a transparent colour key + click-through)
# ---------------------------------------------------------------------------
KEY = "#010203"            # transparent colour key
COLOURS = {
    "next": ("#16a34a", "#ffffff", "#bbf7d0"),   # fill, text, ring
    "step": ("#1d4ed8", "#ffffff", "#1e3a8a"),
    "done": ("#14532d", "#86efac", "#14532d"),
    "skip": ("#7f1d1d", "#fecaca", "#7f1d1d"),
    "doubt": ("#a16207", "#ffffff", "#713f12"),
    "probar": ("#ca8a04", "#111111", "#fde68a"),
}
FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"


class Overlay:
    """Transparent, click-through, capture-excluded drawing layer."""

    def __init__(self, root):
        import tkinter as tk
        self.available = sys.platform == "win32"
        self.hidden_from_capture = False
        self.win = None
        self.bounds: Optional[Rect] = None
        if not self.available:
            return
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=KEY)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            self.available = False
            self.win.destroy()
            self.win = None
            return
        self.canvas = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.geometry("1x1+0+0")
        self.win.update_idletasks()
        self._make_click_through()
        # The window stays mapped (an empty canvas is fully transparent).
        # Hiding/showing it would steal keyboard focus from the browser.

    def _make_click_through(self) -> None:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000
        #        LAYERED      TRANSPARENT  TOOLWINDOW   NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        try:  # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+)
            self.hidden_from_capture = bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))
        except Exception:
            self.hidden_from_capture = False

    def clear(self) -> None:
        if self.win is not None:
            self.canvas.delete("all")

    def show(self, items: List[Item], bounds: Rect) -> None:
        if self.win is None:
            return
        if not items:
            self.clear()
            return
        if bounds != self.bounds:
            l, t, w, h = bounds
            self.win.geometry(f"{w}x{h}+{l}+{t}")
            self.bounds = bounds
        ox, oy = bounds[0], bounds[1]
        c = self.canvas
        c.delete("all")
        for it in items:
            fill, text_col, ring = COLOURS[it.kind]
            x, y, r = it.x - ox, it.y - oy, it.r
            if it.kind == "probar":
                w2 = int(r * 3.4)
                c.create_rectangle(x - w2, y - r, x + w2, y + r, fill=fill, outline=ring, width=3)
                c.create_text(x, y, text=it.text, fill=text_col, font=(FONT, -int(r * 1.1), "bold"))
                continue
            if it.kind == "next":
                c.create_oval(x - r - 5, y - r - 5, x + r + 5, y + r + 5, outline=ring, width=3)
            c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=ring, width=2)
            size = int(r * (1.25 if len(it.text) == 1 else 1.0))
            c.create_text(x, y, text=it.text, fill=text_col, font=(FONT, -size, "bold"))
        self.win.attributes("-topmost", True)   # stay above the browser
