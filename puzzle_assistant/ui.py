"""Floating, always-on-top, dark answer window (Tkinter + Pillow for the
anti-aliased chips and badges).

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
from .overlay import Overlay, money_overlay_items, overlay_items, question_overlay_items
from .qa.memory import Memory
from .settings_ui import open_settings

from . import render as R

BG = R.hexc(R.BG)
PANEL = R.hexc(R.SURFACE)
HOVER = R.hexc(R.SURFACE_2)
FG = R.hexc(R.TEXT)
DIM = R.hexc(R.MUTED)
ACCENT = R.hexc(R.BLUE)
GOOD = R.hexc(R.GREEN_SOFT)
WARN = R.hexc(R.AMBER)
BAD = R.hexc(R.RED)
FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
FONT_SEMI = "Segoe UI Semibold" if sys.platform == "win32" else "DejaVu Sans"


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


def _set_icon(root: tk.Tk) -> None:
    """Window / taskbar icon (assets/app.ico)."""
    import os
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=os.path.join(base, "app.ico"))
        else:
            root.iconphoto(True, tk.PhotoImage(file=os.path.join(base, "app.png")))
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
        self._images = {}                 # keep PhotoImage references alive
        self._view: Optional[dict] = None  # last rendered state (for resizes)

        self.memory = Memory()             # learned questions (SQLite)
        self.engine.memory = self.memory
        self._settings = None
        r = self.root = tk.Tk()
        r.title("SK Assistant")
        self._base_scaling = float(r.tk.call("tk", "scaling"))
        self._apply_text_scale()
        r.configure(bg=BG)
        r.geometry(cfg.window_geometry)
        r.minsize(300, 220)
        _set_icon(r)
        r.attributes("-topmost", bool(cfg.always_on_top))
        try:
            r.attributes("-alpha", float(cfg.opacity))
        except tk.TclError:
            pass
        r.columnconfigure(0, weight=1)

        # ---- header: icon, title, status pill --------------------------------
        head = tk.Frame(r, bg=BG)
        head.grid(row=0, column=0, sticky="we", padx=14, pady=(12, 0))
        head.columnconfigure(1, weight=1)
        self.l_icon = tk.Label(head, bg=BG)
        self.l_icon.grid(row=0, column=0, padx=(0, 8))
        self._set_image(self.l_icon, "icon", _app_icon(18))
        tk.Label(head, text="SK Assistant", bg=BG, fg=FG, font=(FONT_SEMI, 11),
                 anchor="w").grid(row=0, column=1, sticky="w")
        self.l_pill = tk.Label(head, bg=BG)
        self.l_pill.grid(row=0, column=2, sticky="e")
        self.l_target = tk.Label(r, text="", bg=BG, fg=DIM, font=(FONT, 10), anchor="w")
        self.l_target.grid(row=1, column=0, sticky="we", padx=14, pady=(4, 6))

        # ---- main card -------------------------------------------------------
        card = self.card = tk.Frame(r, bg=PANEL, highlightthickness=1,
                                    highlightbackground=R.hexc(R.BORDER))
        card.grid(row=2, column=0, sticky="nswe", padx=12)
        r.rowconfigure(2, weight=1)
        card.columnconfigure(0, weight=1)
        self.l_section = tk.Label(card, text="", bg=PANEL, fg=DIM, font=(FONT_SEMI, 8), anchor="w")
        self.l_section.grid(row=0, column=0, sticky="we", padx=12, pady=(10, 4))
        self.l_main = tk.Label(card, bg=PANEL, fg=DIM, font=(FONT_SEMI, 20), anchor="w",
                               justify="left")
        self.l_main.grid(row=1, column=0, sticky="we", padx=12)
        self.l_next = tk.Label(card, text="", bg=PANEL, fg=FG, font=(FONT, 10), anchor="w",
                               justify="left")
        self.l_next.grid(row=2, column=0, sticky="we", padx=12, pady=(8, 0))
        tagrow = tk.Frame(card, bg=PANEL)
        tagrow.grid(row=3, column=0, sticky="we", padx=12, pady=(6, 0))
        self.l_tag_title = tk.Label(tagrow, text="", bg=PANEL, fg=DIM, font=(FONT, 9))
        self.l_tag_title.pack(side="left")
        self.l_tags = tk.Label(tagrow, bg=PANEL)
        self.l_tags.pack(side="left", padx=(6, 0))
        self.l_hint = tk.Label(card, text="", bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w",
                               justify="left")
        self.l_hint.grid(row=4, column=0, sticky="we", padx=12, pady=(4, 10))

        self.l_warn = tk.Label(r, text="", bg=BG, fg=WARN, font=(FONT, 9), anchor="w",
                               justify="left")
        self.l_warn.grid(row=3, column=0, sticky="we", padx=14, pady=(6, 0))

        # ---- toolbar ---------------------------------------------------------
        bar = tk.Frame(r, bg=BG)
        bar.grid(row=4, column=0, sticky="we", padx=10, pady=(6, 10))
        bar.columnconfigure(9, weight=1)
        self.b_overlay = None
        buttons = {}
        for i, (txt, cmd) in enumerate((("On", self.toggle_power),
                                        ("Rescan", self.engine.rescan),
                                        ("Numbers", self.toggle_overlay),
                                        ("Settings", self.open_settings))):
            b = _flat_button(bar, txt, cmd)
            b.grid(row=0, column=i, padx=(0, 4))
            buttons[txt] = b
        self.b_overlay, self.b_power = buttons["Numbers"], buttons["On"]
        self.l_mode = tk.Label(bar, text="", bg=BG, fg=DIM, font=(FONT, 8))
        self.l_mode.grid(row=0, column=10, sticky="e")

        r.bind("<F5>", lambda e: self.engine.rescan())
        r.bind("<F8>", lambda e: self.engine.snapshot())
        r.bind("<Configure>", self._on_resize)
        r.protocol("WM_DELETE_WINDOW", self.close)
        r.after(50, lambda: _win32_tweaks(r))
        self._last_w = 0
        self._resize_job = None
        self._last_analysis: Optional[Analysis] = None
        self._last_place: Optional[dict] = None
        self._last_money = None
        self._last_question = None
        # Step numbers drawn on the cards (click-through, hidden from capture)
        self.overlay = Overlay(r)
        self.engine.overlay_in_capture = self.overlay.available and not self.overlay.hidden_from_capture
        if not self.overlay.available:
            self.b_overlay.grid_remove()       # needs Windows
        self._update_overlay_button()
        self._update_region_label()
        self._update_power()
        self._waiting("Starting…")

    # -- small helpers ---------------------------------------------------------
    def _set_image(self, label: tk.Label, key: str, pil_img) -> None:
        from PIL import ImageTk
        if pil_img is None:
            label.config(image="")
            self._images.pop(key, None)
            return
        photo = ImageTk.PhotoImage(pil_img)
        self._images[key] = photo
        label.config(image=photo)

    def _pill(self, text: str, colour) -> None:
        self._set_image(self.l_pill, "pill", R.status_pill(text, colour, 22, R.BG))

    def _content_width(self) -> int:
        return max(200, self.root.winfo_width() - 2 * 12 - 2 * 12 - 2)

    # -- actions -------------------------------------------------------------
    def toggle_power(self) -> None:
        self.cfg.assistant_on = not self.cfg.assistant_on
        self.cfg.save()
        self._setting_changed("assistant_on")

    def _update_power(self) -> None:
        on = self.cfg.assistant_on
        self.b_power.config(text="● On" if on else "○ Off", fg=GOOD if on else DIM)
        self.engine.set_paused(not on)
        if not on:
            self._last_place = self._last_analysis = self._last_money = None
            self.overlay.clear()
            self._view = None
            self._pill("Off", R.MUTED)
            self.l_target.config(text="SK Assistant is switched off")
            self.l_section.config(text="")
            self._set_image(self.l_main, "main", None)
            self.l_main.config(text="Paused", fg=DIM, font=(FONT_SEMI, 18))
            self.l_next.config(text="")
            self.l_tag_title.config(text="")
            self._set_image(self.l_tags, "tags", None)
            self.l_hint.config(text="Press “○ Off” to switch it back on.", fg=DIM)
            self.l_warn.config(text="")
        else:
            self._waiting("Waiting for puzzle…")

    def open_settings(self) -> None:
        self._settings = open_settings(
            self._settings, self.root, self.cfg, self.memory, self._setting_changed,
            {"region": self.pick_region, "auto": self.clear_region,
             "snapshot": self.engine.snapshot})

    def _setting_changed(self, name: str) -> None:
        if name == "assistant_on":
            self._update_power()
        elif name == "always_on_top":
            self.root.attributes("-topmost", bool(self.cfg.always_on_top))
        elif name == "text_scale":
            self._apply_text_scale()
            self._rerender()
        elif name == "overlay":
            self._update_overlay_button()
            self._draw_overlay()
        elif name in ("weapon_puzzle", "money_puzzle", "questions"):
            self.engine.rescan()
        elif name == "memory" and self._view is None and self.cfg.assistant_on:
            self._waiting("Waiting for puzzle…")

    def _apply_text_scale(self) -> None:
        """Text size: Tk point sizes follow the 'tk scaling' factor."""
        s = min(1.6, max(0.8, float(self.cfg.text_scale or 1.0)))
        self.root.tk.call("tk", "scaling", self._base_scaling * s)
        self._refresh_fonts(self.root)

    def _refresh_fonts(self, widget) -> None:
        """Re-apply each label's font so the new text size takes effect."""
        for child in widget.winfo_children():
            try:
                f = child.cget("font")
                if f:
                    child.config(font=f)
            except tk.TclError:
                pass
            self._refresh_fonts(child)

    def toggle_overlay(self) -> None:
        self.cfg.overlay = not self.cfg.overlay
        self.cfg.save()
        self._update_overlay_button()
        self._draw_overlay()

    def _update_overlay_button(self) -> None:
        if self.b_overlay is not None:
            self.b_overlay.config(text="Numbers: on" if self.cfg.overlay else "Numbers: off")

    def _draw_overlay(self) -> None:
        p, a, money = self._last_place, self._last_analysis, self._last_money
        qn = getattr(self, "_last_question", None)
        if not self.cfg.overlay or p is None or (a is None and money is None and qn is None):
            self.overlay.clear()
            return
        if qn is not None:
            self.overlay.show(question_overlay_items(*qn, p))
        elif money is not None:
            self.overlay.show(money_overlay_items(money, p))
        else:
            self.overlay.show(overlay_items(a, p["cards"]))

    def pick_region(self) -> None:
        self.engine.set_paused(True)
        self.overlay.clear()
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
        self.l_mode.config(text="region" if self.cfg.region else "read-only")

    def _on_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        w = event.width
        if abs(w - self._last_w) < 4:
            return
        self._last_w = w
        for lbl in (self.l_next, self.l_hint, self.l_warn, self.l_main):
            lbl.config(wraplength=max(150, w - 60))
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(60, self._rerender)

    def _rerender(self) -> None:
        self._resize_job = None
        if self._view and self._view.get("analysis") is not None:
            self._render(self._view["analysis"])
        elif self._view and self._view.get("money") is not None:
            self._render_money(self._view["money"])
        elif self._view and self._view.get("question") is not None:
            self._render_question(*self._view["question"])

    # -- engine messages -----------------------------------------------------
    def _poll(self) -> None:
        latest = None
        try:
            while True:
                latest = self.q.get_nowait()
                if latest.get("state") == "info":
                    self.l_hint.config(text=latest.get("msg", ""), fg=ACCENT)
                    latest = None
                elif latest.get("state") == "overlay_clear":
                    self._last_place = None
                    self.overlay.clear()
                    latest = None
        except queue.Empty:
            pass
        if latest:
            self._show(latest)
        self.root.after(25, self._poll)

    def _show(self, m: dict) -> None:
        if not self.cfg.assistant_on:
            return
        st = m.get("state")
        if st in ("waiting", "searching", "analyzing"):
            self._last_place = self._last_analysis = self._last_money = None
            self._last_question = None
            self.overlay.clear()
            if st == "waiting":
                self._waiting(m.get("msg", "Waiting for puzzle…"))
            else:
                self._pill("Reading…", R.BLUE)
            return
        if st == "error":
            self._pill("Error", R.RED)
            self.l_warn.config(text=m.get("msg", ""), fg=BAD)
            return
        if st == "question_wait":
            self._last_place = self._last_analysis = self._last_money = None
            self.overlay.clear()
            self._view = None
            self._pill("Question", R.BLUE)
            self.l_target.config(text="A question is on screen")
            self.l_section.config(text="")
            self._set_image(self.l_main, "main", None)
            self.l_main.config(text="Press Rescan (F5)", fg=FG, font=(FONT_SEMI, 18))
            self.l_next.config(text="Questions are read only when you ask (Settings).", fg=DIM)
            self.l_hint.config(text="")
            return
        if st == "question":
            self._last_analysis = self._last_money = None
            self._last_place = m.get("place")
            self._last_question = (m.get("question"), m.get("answer"), m.get("status"))
            self._render_question(*self._last_question, extra=m)
            self._draw_overlay()
            return
        if st == "money":
            self._last_analysis, self._last_money = None, m.get("money")
            self._last_place = m.get("place")
            self._render_money(self._last_money)
            self._draw_overlay()
            return
        a: Optional[Analysis] = m.get("analysis")
        if a is None:
            return
        self._last_money = None
        self._render(a)
        self._last_analysis, self._last_place = a, m.get("place")
        self._draw_overlay()

    def _waiting(self, msg: str) -> None:
        self._view = None
        self._pill("Waiting", R.MUTED)
        self.l_target.config(text="Open a puzzle or a question in the game")
        self.l_section.config(text="")
        self._set_image(self.l_main, "main", None)
        self.l_main.config(text="Waiting for puzzle…" if "No puzzle" not in msg else msg,
                           fg=DIM, font=(FONT_SEMI, 18))
        self.l_next.config(text="")
        self.l_tag_title.config(text="")
        self._set_image(self.l_tags, "tags", None)
        st = self.memory.stats()
        self.l_hint.config(text="Weapon assembly, money counting and questions are detected "
                                f"automatically.\nMemory: {st['questions']} learned questions.",
                           fg=DIM)
        err = getattr(getattr(self, "overlay", None), "error", "")
        self.l_warn.config(text=f"Numbers on cards unavailable: {err}" if err else "", fg=WARN)

    def _render(self, a: Analysis) -> None:
        self._view = {"analysis": a}
        s, p = a.solution, a.puzzle
        width = self._content_width()
        if s is None or p is None:
            self._pill("No answer", R.RED)
            self.l_section.config(text="")
            self._set_image(self.l_main, "main", None)
            self.l_main.config(text="Can't read this puzzle", fg=BAD, font=(FONT_SEMI, 16))
            self.l_next.config(text=a.error)
            self.l_hint.config(text="Press F5 to read again", fg=DIM)
            return
        target = p.target_caliber or "?"
        self.l_target.config(text=f"{p.weapon or 'Arma'}  ·  calibre {target}")
        dont = [(l, p.pieces[l].caliber or "") for l in s.excluded]

        if s.ok and s.certain:
            self._pill("Verified", R.GREEN_SOFT)
            self.l_warn.config(text="")
            if s.remaining:
                nums = dict(s.numbering) or {l: i + 1 for i, l in enumerate(s.order)}
                nxt = s.remaining[0]
                steps = [(l, nums.get(l, i + 1),
                          "done" if l in s.done else ("next" if l == nxt else "step"),
                          p.pieces[l].name) for i, l in enumerate(s.order)]
                self.l_section.config(text=f"ORDER  ·  {len(s.done)}/{len(s.order)} DONE"
                                      if s.done else "ORDER")
                self.l_main.config(text="")
                self._set_image(self.l_main, "main", R.chips(steps, width, R.SURFACE))
                name = p.pieces[nxt].name
                self.l_next.config(text=f"Next:  {nxt}" + (f"  ·  {name}" if name else ""), fg=FG)
                self.l_hint.config(text="Then press PROBAR. Pieces turn ✓ as you mount them.",
                                   fg=DIM)
            else:
                self.l_section.config(text="ALL PIECES MOUNTED")
                self.l_main.config(text="")
                self._set_image(self.l_main, "main", R.pill("PROBAR", 40))
                self.l_next.config(text="Press Probar to test the weapon.", fg=FG)
                self.l_hint.config(text="")
            self._tags(dont, width)
            return

        # Not verified: never show an order that looks clickable. A click on a
        # piece that is not ready costs a point.
        check = ", ".join(s.doubtful)
        self._set_image(self.l_main, "main", None)
        self._tags([], width)
        if s.ok:
            self._pill("Not sure", R.AMBER)
            self.l_section.config(text="DON'T FOLLOW YET")
            self.l_main.config(text=f"Check card {check}" if check else "Reading unclear",
                               fg=WARN, font=(FONT_SEMI, 18))
            guess = " → ".join(s.remaining)
            self.l_next.config(text=f"Unverified guess:  {guess}" if guess else "", fg=DIM)
        else:
            self._pill("No answer", R.RED)
            self.l_section.config(text="NOT RELIABLE")
            self.l_main.config(text=f"Check card {check}" if check else "Can't solve this reading",
                               fg=BAD, font=(FONT_SEMI, 18))
            self.l_next.config(text="")
        self.l_hint.config(text="Press F5 to read again.", fg=DIM)
        msgs = [f"✖  {x}" for x in s.problems] + [f"⚠  {x}" for x in s.warnings]
        self.l_warn.config(text="\n".join(msgs[:4]) + ("\n…" if len(msgs) > 4 else ""),
                           fg=BAD if s.problems else WARN)

    def _render_money(self, st) -> None:
        """'Cuenta el dinero': running total, then the verified total."""
        self._view = {"money": st}
        self._set_image(self.l_main, "main", None)
        self._tags([], 0)
        legend = "  ·  ".join(str(v) for v in st.legend)
        self.l_target.config(text="Cuenta el dinero" + (f"   ·   {legend}" if legend else ""))
        values = " + ".join(f"{v}" if sure else f"{v if v is not None else '?'}?"
                            for v, sure in st.items)
        seen = f"{st.counted}/{st.expected}" if st.expected else f"{st.counted}"
        if st.done and st.verified:
            self._pill("Verified", R.GREEN_SOFT)
            self.l_section.config(text=f"TOTAL  ·  {st.counted} ITEMS")
            self.l_main.config(text=f"{st.total}", fg=GOOD, font=(FONT_SEMI, 34))
            self.l_hint.config(text="Type it in yourself, then press ✓.", fg=DIM)
            self.l_warn.config(text="")
        elif st.done:
            self._pill("Not sure", R.AMBER)
            self.l_section.config(text="DON'T TRUST YET")
            self.l_main.config(text=f"{st.total} ?", fg=WARN, font=(FONT_SEMI, 30))
            self.l_hint.config(text="The count doesn't match the game. Check before typing.",
                               fg=DIM)
            self.l_warn.config(text="\n".join(f"⚠  {p}" for p in st.problems[:4]), fg=WARN)
        else:
            self._pill("Counting", R.BLUE)
            self.l_section.config(text=f"RUNNING TOTAL  ·  SEEN {seen}")
            self.l_main.config(text=f"{st.total}" if st.counted else "…", fg=FG,
                               font=(FONT_SEMI, 30))
            self.l_hint.config(text="Wait until the strip is empty.", fg=DIM)
            self.l_warn.config(text="")
        self.l_next.config(text=values, fg=DIM if not st.done else FG)

    def _render_question(self, q, ans, status: str = "", extra: Optional[dict] = None) -> None:
        """A recognised question and its answer.
        status: 'memory' | 'solver' | 'game' | 'ai' | 'unsure' | 'thinking' |
                'unknown' (not learned yet) | 'answered' (result not readable)"""
        extra = extra or {}
        self._view = {"question": (q, ans, status)}
        self._set_image(self.l_main, "main", None)
        self._tags([], 0)
        label = {"memory": ("From memory", R.GREEN_SOFT), "game": ("Learned ✓", R.GREEN_SOFT),
                 "solver": ("Solved", R.GREEN_SOFT), "ai": ("AI · unverified", R.AMBER),
                 "unsure": ("Not sure", R.AMBER), "thinking": ("Thinking…", R.BLUE),
                 "unknown": ("New question", R.AMBER), "answered": ("Answered", R.MUTED)}
        text, colour = label.get(status, ("Question", R.BLUE))
        self._pill(text, colour)
        self.l_target.config(text=(q.category or "Pregunta").upper())
        self.l_section.config(text="CORRECT ANSWER" if status == "game" else "ANSWER")
        if ans is None:
            msg = {"unknown": "Not learned yet", "answered": "Result not readable"}.get(status, "…")
            self.l_main.config(text=msg, fg=WARN if status == "unknown" else DIM,
                               font=(FONT_SEMI, 16))
            self.l_next.config(text=q.text + (("\n" + "  ·  ".join(q.clues)) if q.clues else ""),
                               fg=FG)
            self.l_hint.config(text="Answer it in the game: SK Assistant remembers the correct "
                                    "answer for next time." if status == "unknown" else "",
                               fg=DIM)
            self.l_warn.config(text="")
            return
        else:
            shown = extra.get("display") or ans          # clean learned text when available
            size = 16
            if ans.kind == "choice" and ans.choice in q.options:
                letter = "ABCDEFGH"[q.options.index(ans.choice)]
                main = f"{letter}.  {shown.choice or ans.choice}"
            elif ans.kind == "pairs":
                main = "\n".join(f"{a}  →  {b}" for a, b in (shown.pairs or ans.pairs).items())
                size = 13
            elif ans.kind == "order":
                main = "\n".join(f"{i}.  {x}" for i, x in enumerate(shown.order or ans.order, 1))
                size = 13
            else:
                main = ans.summary()
            sure = status in ("memory", "game", "solver")
            self.l_main.config(text=main, fg=GOOD if sure else WARN, font=(FONT_SEMI, size))
        clues = ("\n" + "  ·  ".join(q.clues)) if q.clues else ""
        self.l_next.config(text=q.text + clues, fg=FG)
        self.l_hint.config(text=(ans.explanation if ans and ans.explanation else ""), fg=DIM)
        self.l_warn.config(text="")

    def _tags(self, items, width: int) -> None:
        img = R.tags(items, 22, R.RED, R.SURFACE) if items else None
        self.l_tag_title.config(text="Don't click" if items else "")
        self._set_image(self.l_tags, "tags", img)

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
            self.memory.close()
            self.root.destroy()


def _flat_button(parent, text: str, cmd) -> tk.Label:
    """Flat, rounded-looking text button with hover highlight."""
    b = tk.Label(parent, text=text, bg=PANEL, fg=DIM, font=(FONT, 9), padx=10, pady=4,
                 cursor="hand2")
    b.bind("<Button-1>", lambda e: cmd())
    b.bind("<Enter>", lambda e: b.config(bg=HOVER, fg=FG))
    b.bind("<Leave>", lambda e: b.config(bg=PANEL, fg=DIM))
    return b


def _app_icon(size: int):
    import os
    from PIL import Image
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "app.png")
    try:
        return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None
