"""Floating, always-on-top, dark answer window (Tkinter, no extra dependencies).

The window only DISPLAYS information. It never sends clicks or keystrokes to
any other program.
"""
from __future__ import annotations

import queue
import sys
import tkinter as tk
from typing import Optional

from .analyzer import Analysis
from .config import Config
from .engine import Engine

BG = "#15171c"
PANEL = "#1e2129"
FG = "#e8eaf0"
DIM = "#8a90a0"
ACCENT = "#5aa9ff"
GOOD = "#4ade80"
WARN = "#fbbf24"
BAD = "#f87171"
FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"


def _win32_tweaks(root: tk.Tk) -> None:
    """Dark title bar + hide this window from screen capture (so it can never
    cover/confuse the puzzle it is reading). Purely cosmetic / self-affecting."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


class RegionSelector:
    """Full-screen translucent overlay: drag a rectangle around the puzzle."""

    def __init__(self, root: tk.Tk, on_done):
        from .capture import new_mss
        with new_mss() as sct:
            v = sct.monitors[0]
        self.left, self.top = v["left"], v["top"]
        self.on_done = on_done
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.30)
        self.win.geometry(f"{v['width']}x{v['height']}+{v['left']}+{v['top']}")
        self.cv = tk.Canvas(self.win, bg="black", highlightthickness=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)
        self.cv.create_text(v["width"] // 2, 40, fill="white", font=(FONT, 18, "bold"),
                            text="Drag a box around the whole puzzle window  ·  Esc = cancel")
        self.start = None
        self.rect = None
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.focus_force()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#5aa9ff", width=3)

    def _drag(self, e):
        if self.start:
            self.cv.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        x, y, w, h = min(x0, e.x), min(y0, e.y), abs(e.x - x0), abs(e.y - y0)
        self._finish([x + self.left, y + self.top, w, h] if w > 40 and h > 40 else None)

    def _finish(self, region):
        self.win.destroy()
        self.on_done(region)


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.q: "queue.Queue[dict]" = queue.Queue()
        self.engine = Engine(cfg, self.q)

        r = self.root = tk.Tk()
        r.title("Puzzle Answer Assistant")
        r.configure(bg=BG)
        r.geometry(cfg.window_geometry)
        r.minsize(260, 190)
        r.attributes("-topmost", True)
        try:
            r.attributes("-alpha", float(cfg.opacity))
        except tk.TclError:
            pass

        pad = {"padx": 10, "sticky": "we"}
        r.columnconfigure(0, weight=1)
        self.l_title = tk.Label(r, text="PUZZLE ANSWER ASSISTANT", bg=BG, fg=ACCENT,
                                font=(FONT, 9, "bold"), anchor="w")
        self.l_title.grid(row=0, column=0, pady=(6, 0), **pad)
        self.l_status = tk.Label(r, text="Starting…", bg=BG, fg=FG, font=(FONT, 10), anchor="w")
        self.l_status.grid(row=1, column=0, **pad)
        self.l_target = tk.Label(r, text="", bg=BG, fg=FG, font=(FONT, 11, "bold"), anchor="w")
        self.l_target.grid(row=2, column=0, **pad)

        box = self.box = tk.Frame(r, bg=PANEL)
        box.grid(row=3, column=0, padx=8, pady=4, sticky="nswe")
        r.rowconfigure(3, weight=1)
        box.columnconfigure(0, weight=1)
        self.l_order_hdr = tk.Label(box, text="RECOMMENDED ORDER:", bg=PANEL, fg=DIM,
                                    font=(FONT, 8, "bold"), anchor="w")
        self.l_order_hdr.grid(row=0, column=0, sticky="we", padx=8, pady=(4, 0))
        self.l_order = tk.Label(box, text="Waiting for puzzle…", bg=PANEL, fg=DIM,
                                font=(FONT, 22, "bold"), anchor="w", justify="left")
        self.l_order.grid(row=1, column=0, sticky="we", padx=8)
        self.l_names = tk.Label(box, text="", bg=PANEL, fg=DIM, font=(FONT, 8),
                                anchor="w", justify="left")
        self.l_names.grid(row=2, column=0, sticky="we", padx=8)
        self.l_aside = tk.Label(box, text="", bg=PANEL, fg=FG, font=(FONT, 10),
                                anchor="w", justify="left")
        self.l_aside.grid(row=3, column=0, sticky="we", padx=8, pady=(2, 4))

        self.l_warn = tk.Label(r, text="", bg=BG, fg=WARN, font=(FONT, 8),
                               anchor="w", justify="left")
        self.l_warn.grid(row=4, column=0, **pad)

        bar = tk.Frame(r, bg=BG)
        bar.grid(row=5, column=0, sticky="we", padx=6, pady=(2, 6))
        for i, (txt, cmd) in enumerate((("Rescan (F5)", self.engine.rescan),
                                        ("Region", self.pick_region),
                                        ("Auto", self.clear_region),
                                        ("Snapshot", self.engine.snapshot))):
            tk.Button(bar, text=txt, command=cmd, bg="#2a2e38", fg=FG, bd=0, padx=6, pady=1,
                      activebackground="#3a3f4c", activeforeground=FG,
                      font=(FONT, 8)).grid(row=0, column=i, padx=2)
        self.l_mode = tk.Label(bar, text="Status: Read-only mode", bg=BG, fg=DIM, font=(FONT, 8))
        self.l_mode.grid(row=0, column=10, padx=(8, 0))
        bar.columnconfigure(9, weight=1)

        r.bind("<F5>", lambda e: self.engine.rescan())
        r.bind("<F8>", lambda e: self.engine.snapshot())
        r.bind("<Configure>", self._on_resize)
        r.protocol("WM_DELETE_WINDOW", self.close)
        r.after(50, lambda: _win32_tweaks(r))
        self._last_w = 0
        self._update_region_label()

    # -- actions -------------------------------------------------------------
    def pick_region(self) -> None:
        self.engine.set_paused(True)
        self.root.withdraw()

        def done(region):
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            if region:
                self.cfg.region = region
                self.cfg.save()
                self.engine.region_changed.set()
            self._update_region_label()
            self.engine.set_paused(False)
            self.engine.rescan()

        self.root.after(150, lambda: RegionSelector(self.root, done))

    def clear_region(self) -> None:
        self.cfg.region = None
        self.cfg.save()
        self.engine.region_changed.set()
        self._update_region_label()
        self.engine.rescan()

    def _update_region_label(self) -> None:
        reg = "region" if self.cfg.region else "auto"
        self.l_mode.config(text=f"Read-only mode · {reg}")

    def _on_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        w = event.width
        if abs(w - self._last_w) < 4:
            return
        self._last_w = w
        size = max(14, min(48, int(w / 17)))
        self.l_order.config(font=(FONT, size, "bold"), wraplength=w - 36)
        for lbl in (self.l_names, self.l_aside, self.l_warn, self.l_status):
            lbl.config(wraplength=w - 24)

    # -- engine messages -----------------------------------------------------
    def _poll(self) -> None:
        latest = None
        try:
            while True:
                latest = self.q.get_nowait()
                if latest.get("state") == "info":
                    self.l_status.config(text=latest.get("msg", ""), fg=ACCENT)
                    latest = None
        except queue.Empty:
            pass
        if latest:
            self._show(latest)
        self.root.after(25, self._poll)

    def _show(self, m: dict) -> None:
        st = m.get("state")
        if st in ("waiting", "searching", "analyzing"):
            self.l_status.config(text="Status: " + m.get("msg", ""), fg=FG if st != "waiting" else DIM)
            if st == "waiting":
                self._clear()
            return
        if st == "error":
            self.l_status.config(text="Error: " + m.get("msg", ""), fg=BAD)
            return
        a: Optional[Analysis] = m.get("analysis")
        if a is None:
            return
        self._render(a)

    def _clear(self) -> None:
        self.l_target.config(text="")
        self.l_order.config(text="Waiting for puzzle…", fg=DIM)
        self.l_names.config(text="")
        self.l_aside.config(text="")
        self.l_warn.config(text="")

    def _render(self, a: Analysis) -> None:
        s, p = a.solution, a.puzzle
        if s is None or p is None:
            self.l_status.config(text=f"Status: Puzzle detected – not readable ({a.error})", fg=BAD)
            self.l_order.config(text="?", fg=BAD)
            self.l_names.config(text="")
            self.l_aside.config(text="Press F5 to rescan, or use Region")
            self.l_warn.config(text="")
            return
        tgt = p.target_caliber or "?"
        self.l_target.config(text=f"Target caliber: {tgt}" + (f"   ·   {p.weapon}" if p.weapon else ""))
        order = " → ".join(s.order) if s.order else "?"
        if s.ok and s.certain:
            colour, status = GOOD, "Puzzle detected ✓ verified"
        elif s.ok:
            colour, status = WARN, "UNVERIFIED – check warnings"
        else:
            colour, status = BAD, "NOT RELIABLE – see problems"
        self.l_status.config(text=f"Status: {status}  ({a.ms:.0f} ms)", fg=colour)
        self.l_order.config(text=order, fg=colour)
        names = [f"{l} {p.pieces[l].name}" for l in s.order if l in p.pieces and p.pieces[l].name]
        self.l_names.config(text=" · ".join(names))
        aside = "Then: test the weapon (probar el arma)" if s.order else ""
        if s.excluded:
            aside = "Set aside (other caliber): " + ", ".join(
                f"{l}({p.pieces[l].caliber})" for l in s.excluded) + "\n" + aside
        self.l_aside.config(text=aside)
        msgs = [f"✖ {x}" for x in s.problems] + [f"⚠ {x}" for x in s.warnings]
        self.l_warn.config(text="\n".join(msgs[:4]) + ("\n…" if len(msgs) > 4 else ""),
                           fg=BAD if s.problems else WARN)

    # -- lifecycle -------------------------------------------------------------
    def run(self) -> None:
        self.engine.start()
        self.root.after(25, self._poll)
        self.root.mainloop()

    def close(self) -> None:
        try:
            self.cfg.window_geometry = self.root.geometry()
            self.cfg.save()
        finally:
            self.engine.stop()
            self.root.destroy()
