"""A frame that scrolls up and down when the window is smaller than its
content (mouse wheel or the thin scrollbar on the right); when there is
room, the content simply fills the space."""
from __future__ import annotations

import sys
import tkinter as tk


class ScrollFrame:
    def __init__(self, parent, bg: str):
        self.outer = tk.Frame(parent, bg=bg)
        self.canvas = tk.Canvas(self.outer, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview, width=10)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._bar_shown = False
        self.canvas.bind("<Configure>", lambda e: self._fit())
        self.inner.bind("<Configure>", lambda e: self._fit())
        self.outer.bind("<Enter>", lambda e: self._wheel(True))
        self.outer.bind("<Leave>", lambda e: self._wheel(False))

    def _fit(self) -> None:
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        need = self.inner.winfo_reqheight()
        # fill the space when it fits; otherwise the content's own height + scrolling
        c.itemconfigure(self._win, width=w, height=max(h, need))
        c.configure(scrollregion=(0, 0, w, max(h, need)))
        show = need > h + 1
        if show != self._bar_shown:
            self._bar_shown = show
            if show:
                self.bar.pack(side="right", fill="y", before=c)
            else:
                self.bar.pack_forget()
                c.yview_moveto(0)

    def _wheel(self, on: bool) -> None:
        c = self.canvas
        if on:
            if sys.platform.startswith("linux"):
                c.bind_all("<Button-4>", lambda e: self._scroll(-1))
                c.bind_all("<Button-5>", lambda e: self._scroll(1))
            else:
                c.bind_all("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1))
        else:
            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                c.unbind_all(ev)

    def _scroll(self, step: int) -> None:
        if self._bar_shown:
            self.canvas.yview_scroll(step * 2, "units")
