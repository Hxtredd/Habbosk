"""Settings panel (one window, plain words, dark style like the assistant)."""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Optional

from . import render as R
from . import secrets
from .config import Config

BG = R.hexc(R.BG)
PANEL = R.hexc(R.SURFACE)
HOVER = R.hexc(R.SURFACE_2)
FG = R.hexc(R.TEXT)
DIM = R.hexc(R.MUTED)
ACCENT = R.hexc(R.BLUE)
GOOD = R.hexc(R.GREEN_SOFT)
WARN = R.hexc(R.AMBER)
FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
FONT_SEMI = "Segoe UI Semibold" if sys.platform == "win32" else "DejaVu Sans"

PROVIDERS = [("none", "No AI (memory and built-in solvers only)"),
             ("gemini", "Google Gemini"),
             ("anthropic", "Anthropic Claude"),
             ("openai", "OpenAI")]
SEARCH = [("none", "No internet search"), ("tavily", "Tavily"), ("brave", "Brave Search")]


class SettingsWindow:
    def __init__(self, root: tk.Tk, cfg: Config, memory, on_change: Callable[[str], None],
                 actions: dict):
        """on_change(name) is called after a setting changed (already saved).
        actions: {'region': fn, 'auto': fn, 'snapshot': fn}"""
        self.cfg, self.memory, self.on_change, self.actions = cfg, memory, on_change, actions
        w = self.win = tk.Toplevel(root)
        w.title("SK Assistant – Settings")
        w.configure(bg=BG)
        w.geometry("460x640")
        w.minsize(400, 400)
        w.attributes("-topmost", bool(cfg.always_on_top))
        w.transient(root)

        canvas = tk.Canvas(w, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(w, orient="vertical", command=canvas.yview)
        self.body = tk.Frame(canvas, bg=BG)
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win_id = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        w.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        w.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        w.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        self._build()

    # -- building blocks -------------------------------------------------------
    def _section(self, title: str, note: str = "") -> tk.Frame:
        tk.Label(self.body, text=title.upper(), bg=BG, fg=DIM, font=(FONT_SEMI, 8),
                 anchor="w").pack(fill="x", padx=16, pady=(14, 4))
        f = tk.Frame(self.body, bg=PANEL, highlightthickness=1,
                     highlightbackground=R.hexc(R.BORDER))
        f.pack(fill="x", padx=12)
        if note:
            tk.Label(f, text=note, bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w",
                     justify="left", wraplength=400).pack(fill="x", padx=12, pady=(8, 0))
        return f

    def _check(self, parent, text: str, attr: str) -> tk.BooleanVar:
        var = tk.BooleanVar(value=bool(getattr(self.cfg, attr)))

        def changed():
            setattr(self.cfg, attr, bool(var.get()))
            self._saved(attr)
        tk.Checkbutton(parent, text=text, variable=var, command=changed, bg=PANEL, fg=FG,
                       selectcolor=HOVER, activebackground=PANEL, activeforeground=FG,
                       font=(FONT, 10), anchor="w", bd=0, highlightthickness=0
                       ).pack(fill="x", padx=10, pady=3)
        return var

    def _radio(self, parent, attr: str, choices) -> tk.StringVar:
        var = tk.StringVar(value=str(getattr(self.cfg, attr)))

        def changed():
            setattr(self.cfg, attr, var.get())
            self._saved(attr)
        for value, label in choices:
            tk.Radiobutton(parent, text=label, value=value, variable=var, command=changed,
                           bg=PANEL, fg=FG, selectcolor=HOVER, activebackground=PANEL,
                           activeforeground=FG, font=(FONT, 10), anchor="w", bd=0,
                           highlightthickness=0).pack(fill="x", padx=10, pady=2)
        return var

    def _button(self, parent, text: str, cmd) -> tk.Label:
        b = tk.Label(parent, text=text, bg=HOVER, fg=FG, font=(FONT, 9), padx=10, pady=4,
                     cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.config(bg=R.hexc(R.BORDER)))
        b.bind("<Leave>", lambda e: b.config(bg=HOVER))
        return b

    def _key_row(self, parent, key_name: str, label: str) -> None:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(6, 4))
        tk.Label(row, text=label, bg=PANEL, fg=DIM, font=(FONT, 9)).pack(side="left")
        entry = tk.Entry(row, show="•", bg=HOVER, fg=FG, insertbackground=FG, relief="flat",
                         font=(FONT, 10), width=18)
        entry.pack(side="left", padx=6, fill="x", expand=True)
        status = tk.Label(parent, text="", bg=PANEL, fg=DIM, font=(FONT, 8), anchor="w")
        status.pack(fill="x", padx=12, pady=(0, 6))

        def refresh():
            if secrets.has_key(key_name):
                how = "encrypted with your Windows account" if secrets.encrypted() else \
                    "stored privately (not encrypted on this system)"
                status.config(text=f"Key saved ({how}).", fg=GOOD)
            else:
                status.config(text="No key saved.", fg=DIM)

        def save():
            if entry.get().strip():
                secrets.set_key(key_name, entry.get())
                entry.delete(0, "end")
                refresh()
                self.on_change("keys")

        def remove():
            secrets.set_key(key_name, None)
            refresh()
            self.on_change("keys")
        self._button(row, "Save", save).pack(side="left", padx=(0, 4))
        self._button(row, "Remove", remove).pack(side="left")
        refresh()

    def _saved(self, attr: str) -> None:
        self.cfg.save()
        self.on_change(attr)

    # -- content -----------------------------------------------------------------
    def _build(self) -> None:
        cfg = self.cfg
        f = self._section("General")
        self._check(f, "Assistant on", "assistant_on")
        self._check(f, "Keep on top of other windows", "always_on_top")
        self._check(f, "Show numbers / answers on the game screen", "overlay")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(4, 10))
        tk.Label(row, text="Text size", bg=PANEL, fg=FG, font=(FONT, 10)).pack(side="left")
        scale = tk.Scale(row, from_=80, to=160, resolution=10, orient="horizontal",
                         bg=PANEL, fg=FG, troughcolor=HOVER, highlightthickness=0, bd=0,
                         showvalue=True, length=200, font=(FONT, 8))
        scale.set(int(round(cfg.text_scale * 100)))
        scale.pack(side="left", padx=10)

        def text_size(_e=None):
            cfg.text_scale = scale.get() / 100.0
            self._saved("text_scale")
        scale.bind("<ButtonRelease-1>", text_size)

        self._build_updates()

        f = self._section("What SK Assistant helps with")
        self._check(f, "Weapon puzzle (Montar un arma)", "weapon_puzzle")
        self._check(f, "Money puzzle (Cuenta el dinero)", "money_puzzle")
        self._check(f, "Questions (library, general knowledge)", "questions")
        self._check(f, "World Atlas: learn rooms while I play (Live Explorer)", "world_atlas")
        tk.Label(f, text="Recognise questions:", bg=PANEL, fg=DIM, font=(FONT, 9),
                 anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        self._radio(f, "question_mode", [("auto", "Automatically"),
                                         ("manual", "Only when I press “Scan”")])
        tk.Frame(f, bg=PANEL, height=6).pack()

        f = self._section(
            "Work helper",
            "Does the job's walking tasks (ammunition, sweeping) while you are at the PC. "
            "Starts only with “Start working”; any mouse or keyboard use stops it, it asks "
            "“¿Sigues ahí?” now and then, and pauses when someone mentions your name.")

        def entry_row(label, attr, width, conv):
            row = tk.Frame(f, bg=PANEL)
            row.pack(fill="x", padx=12, pady=(2, 4))
            tk.Label(row, text=label, bg=PANEL, fg=FG, font=(FONT, 10)).pack(side="left")
            e = tk.Entry(row, width=width, bg=HOVER, fg=FG, insertbackground=FG, relief="flat",
                         font=(FONT, 10))
            e.insert(0, str(getattr(cfg, attr)))
            e.pack(side="left", padx=(6, 0))

            def set_value(_e=None):
                try:
                    setattr(cfg, attr, conv(e.get().strip()))
                except ValueError:
                    pass
                e.delete(0, "end")
                e.insert(0, str(getattr(cfg, attr)))
                self._saved(attr)
            e.bind("<FocusOut>", set_value)
            e.bind("<Return>", set_value)

        entry_row("My Habbo name:", "work_my_name", 18, str)
        entry_row("Type :beber when energy is below", "work_energy_min", 5,
                  lambda v: max(5, min(300, int(v))))
        entry_row("Stop after (minutes)", "work_session_minutes", 5,
                  lambda v: max(5, min(240, int(v))))
        tk.Frame(f, bg=PANEL, height=6).pack()

        f = self._section(
            "AI and internet",
            "Used only for new questions the memory and built-in solvers can't answer. "
            "AI answers are marked “AI · unverified”: check them before clicking. "
            "Gemini has a free tier (keep billing off in your Google project to never pay). "
            "Claude and OpenAI always cost money and need “Allow paid AI use”.")
        self._radio(f, "ai_provider", PROVIDERS)
        self._key_row(f, "ai:" + "gemini", "Gemini key")
        self._key_row(f, "ai:" + "anthropic", "Claude key")
        self._key_row(f, "ai:" + "openai", "OpenAI key")
        self._check(f, "Allow paid AI use (off = free tiers only, never pay)", "paid_api")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(2, 6))
        tk.Label(row, text="Monthly limit for paid use: $", bg=PANEL, fg=FG,
                 font=(FONT, 10)).pack(side="left")
        limit = tk.Entry(row, width=7, bg=HOVER, fg=FG, insertbackground=FG, relief="flat",
                         font=(FONT, 10))
        limit.insert(0, f"{cfg.monthly_limit:.2f}")
        limit.pack(side="left")

        def set_limit(_e=None):
            try:
                cfg.monthly_limit = max(0.0, float(limit.get().replace(",", ".")))
            except ValueError:
                pass
            limit.delete(0, "end")
            limit.insert(0, f"{cfg.monthly_limit:.2f}")
            self._saved("monthly_limit")
        limit.bind("<FocusOut>", set_limit)
        limit.bind("<Return>", set_limit)
        self._check(f, "Search the internet when the AI is unsure", "web_search")
        self._radio(f, "search_provider", SEARCH)
        self._key_row(f, "search:tavily", "Tavily key")
        self._key_row(f, "search:brave", "Brave key")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(4, 0))
        l_test = tk.Label(f, text="", bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w",
                          justify="left", wraplength=380)

        def test_ai():
            from .qa.ai import AIAnswerer
            l_test.config(text="Testing… (one small question)", fg=DIM)

            def work():
                msg = AIAnswerer(cfg, self.memory).test()
                try:
                    self.win.after(0, lambda: l_test.config(
                        text=msg, fg=GOOD if msg.startswith("Working") else WARN))
                except tk.TclError:
                    pass                      # settings window closed meanwhile
            threading.Thread(target=work, daemon=True).start()
        self._button(row, "Test AI", test_ai).pack(side="left")
        l_test.pack(fill="x", padx=12, pady=(2, 0))
        req, cost = self.memory.month_usage()
        tk.Label(f, text=f"This month: {req} AI/search requests · estimated ${cost:.2f}",
                 bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w").pack(fill="x", padx=12, pady=(4, 10))

        f = self._section("Memory")
        self.l_mem = tk.Label(f, text="", bg=PANEL, fg=FG, font=(FONT, 10), anchor="w")
        self.l_mem.pack(fill="x", padx=12, pady=(10, 6))
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(0, 10))
        self._button(row, "Export…", self._export).pack(side="left", padx=(0, 6))
        self._button(row, "Import…", self._import).pack(side="left", padx=(0, 6))
        self._button(row, "Forget everything…", self._clear).pack(side="left")
        self._refresh_memory()

        f = self._section("Screen")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=10)
        self._button(row, "Select region…", self.actions["region"]).pack(side="left", padx=(0, 6))
        self._button(row, "All screens", self.actions["auto"]).pack(side="left", padx=(0, 6))
        self._button(row, "Save snapshot (F8)", self.actions["snapshot"]).pack(side="left")
        where = "a selected region" if cfg.region else "every screen"
        tk.Label(f, text=f"Now watching: {where}.", bg=PANEL, fg=DIM, font=(FONT, 9),
                 anchor="w").pack(fill="x", padx=12, pady=(0, 10))
        tk.Frame(self.body, bg=BG, height=16).pack()

    def _build_updates(self) -> None:
        from . import __version__, updater
        f = self._section("Updates")
        tk.Label(f, text=f"This is version {__version__}.", bg=PANEL, fg=FG, font=(FONT, 10),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 0))
        self._check(f, "Look for new versions when SK starts",
                    "check_updates")
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(2, 4))
        status = tk.Label(f, text="", bg=PANEL, fg=DIM, font=(FONT, 9), anchor="w",
                          justify="left", wraplength=380)
        found = {}

        def say(text, colour=DIM):
            try:
                self.win.after(0, lambda: status.config(text=text, fg=colour))
            except tk.TclError:
                pass

        def check():
            say("Checking…")

            def work():
                try:
                    rel = updater.check()
                except Exception as exc:
                    say(f"Could not check: {exc}", WARN)
                    return
                if rel is None:
                    say("You have the newest version ✓", GOOD)
                    return
                found["rel"] = rel
                say(f"Version {rel.version} is available ({rel.size / 1e6:.0f} MB).\n{rel.notes}\n"
                    "Press “Update now”.", WARN)
                self.win.after(0, lambda: upd.pack(side="left", padx=(6, 0)))
            threading.Thread(target=work, daemon=True).start()

        def update():
            rel = found.get("rel")
            if rel is None:
                return
            upd.pack_forget()

            def work():
                try:
                    say("Downloading… 0 %")
                    path = updater.download(rel, lambda x: say(f"Downloading… {x * 100:.0f} %"))
                    say("Installing…")
                    changed = updater.install(path)
                    updater.update_packages(changed)
                except Exception as exc:
                    say(f"Update failed, nothing was changed that can't be undone: {exc}", WARN)
                    return
                say(f"Updated to {rel.version} ({len(changed)} files). Restarting…", GOOD)
                self.win.after(1200, self.actions.get("restart", lambda: None))
            threading.Thread(target=work, daemon=True).start()

        self._button(row, "Check for updates", check).pack(side="left")
        upd = self._button(row, "Update now", update)
        status.pack(fill="x", padx=12, pady=(0, 10))
        if self.actions.get("update_found"):
            found["rel"] = self.actions["update_found"]()
            if found["rel"] is not None:
                rel = found["rel"]
                status.config(text=f"Version {rel.version} is available.\n{rel.notes}", fg=WARN)
                upd.pack(side="left", padx=(6, 0))

    def _refresh_memory(self) -> None:
        st = self.memory.stats()
        solved = st.get("solver_right", 0) + st.get("solver_wrong", 0)
        self.l_mem.config(text=f"Remembers {st['questions']} questions "
                               f"({st['verified']} confirmed by the game) · "
                               f"{st['corrections']} corrections\n"
                               f"Built-in solving checked by the game: {st.get('solver_right', 0)} "
                               f"right, {st.get('solver_wrong', 0)} wrong"
                               + ("" if solved else " (none yet)")
                               + f"\nAI answers checked by the game: {st.get('ai_right', 0)} right, "
                               f"{st.get('ai_wrong', 0)} wrong", justify="left")

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".json",
                                            initialfile="habbosk-memory.json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            n = self.memory.export_json(path)
            messagebox.showinfo("SK Assistant", f"Exported {n} questions.", parent=self.win)

    def _import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.win, title="Add questions from an older memory or an export",
            filetypes=[("SK Assistant memory", "*.db *.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            c = self.memory.merge_from(path)
        except Exception as exc:
            messagebox.showerror("SK Assistant", f"Could not read that file:\n{exc}", parent=self.win)
            return
        self._refresh_memory()
        self.on_change("memory")
        messagebox.showinfo("SK Assistant",
                            f"{c['new']} new questions added, {c['updated']} improved, "
                            f"{c['same'] + c['blocked']} already known."
                            + (f"\n{c['skipped']} left out (sum questions read by an older "
                               "version, or unreadable entries)." if c["skipped"] else ""),
                            parent=self.win)

    def _clear(self) -> None:
        if messagebox.askyesno("SK Assistant", "Forget every learned question and the history?\n"
                               "This cannot be undone.", parent=self.win):
            self.memory.clear()
            self._refresh_memory()
            self.on_change("memory")

    def lift(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()


def open_settings(existing: Optional[SettingsWindow], *args, **kw) -> SettingsWindow:
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.lift()
                return existing
        except tk.TclError:
            pass
    return SettingsWindow(*args, **kw)
