"""Live Explorer / World Atlas window (Tk). Read-only: it only shows what SK
recognised and lets the owner accept or correct new rooms."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Optional

from .. import render as R

BG = R.hexc(R.BG)
PANEL = R.hexc(R.SURFACE)
HOVER = R.hexc(R.SURFACE_2)
FG = R.hexc(R.TEXT)
DIM = R.hexc(R.MUTED)
GOOD = R.hexc(R.GREEN_SOFT)
WARN = R.hexc(R.AMBER)
ACCENT = R.hexc(R.BLUE)
KIND = {"place": "Place", "street": "Street", "player": "Player room", "generated": "Generated",
        None: "-"}


class ExplorerWindow:
    def __init__(self, root: tk.Tk, atlas, explorer, font, topmost: bool = True):
        self.atlas, self.explorer, self.font = atlas, explorer, font
        w = self.win = tk.Toplevel(root)
        # stay above the browser like the main window (it vanished behind
        # Chrome when the game was clicked)
        w.attributes("-topmost", bool(topmost))
        w.after(200, lambda: _hide_from_capture(w))
        w.title("SK Assistant – Live Explorer")
        w.configure(bg=BG)
        w.geometry("560x680")
        w.minsize(420, 480)
        self._build()
        self._tick()

    def lift(self) -> None:
        self.win.deiconify()
        self.win.lift()

    # -- layout ----------------------------------------------------------------
    def _section(self, title: str) -> tk.Frame:
        tk.Label(self.win, text=title.upper(), bg=BG, fg=DIM, font=self.font(8),
                 anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        f = tk.Frame(self.win, bg=PANEL)
        f.pack(fill="x", padx=12)
        return f

    def _button(self, parent, text, cmd) -> tk.Label:
        b = tk.Label(parent, text=text, bg=HOVER, fg=FG, font=self.font(9), padx=10, pady=3,
                     cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def _build(self) -> None:
        f = self._section("Live")
        self.l_screen = tk.Label(f, bg=PANEL, fg=FG, font=self.font(10), anchor="w", justify="left")
        self.l_screen.pack(fill="x", padx=12, pady=(8, 0))
        self.l_room = tk.Label(f, bg=PANEL, fg=GOOD, font=self.font(15, True), anchor="w",
                               justify="left")
        self.l_room.pack(fill="x", padx=12)
        self.l_info = tk.Label(f, bg=PANEL, fg=DIM, font=self.font(9), anchor="w", justify="left")
        self.l_info.pack(fill="x", padx=12, pady=(0, 8))

        f = self._section("New rooms waiting for you")
        self.pend = tk.Frame(f, bg=PANEL)
        self.pend.pack(fill="x", padx=12, pady=8)

        f = self._section("World Atlas")
        self.l_stats = tk.Label(f, bg=PANEL, fg=FG, font=self.font(9), anchor="w", justify="left")
        self.l_stats.pack(fill="x", padx=12, pady=(8, 4))
        row = tk.Frame(f, bg=PANEL)
        row.pack(fill="x", padx=12, pady=(0, 6))
        self.search = tk.Entry(row, bg=HOVER, fg=FG, insertbackground=FG, relief="flat",
                               font=self.font(10))
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self._fill_rooms())
        self._button(row, "Export…", self._export).pack(side="left", padx=(6, 0))
        lst = tk.Frame(f, bg=PANEL)
        lst.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.rooms = tk.Listbox(lst, bg=HOVER, fg=FG, selectbackground=ACCENT, relief="flat",
                                font=self.font(9), height=10, activestyle="none",
                                highlightthickness=0)
        sb = tk.Scrollbar(lst, command=self.rooms.yview)
        self.rooms.config(yscrollcommand=sb.set)
        self.rooms.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.rooms.bind("<<ListboxSelect>>", lambda e: self._show_room())
        self.l_detail = tk.Label(f, bg=PANEL, fg=FG, font=self.font(9), anchor="w",
                                 justify="left", wraplength=500)
        self.l_detail.pack(fill="x", padx=12, pady=(0, 10))
        self._room_ids = []
        self._pending_key = None
        self._fill_rooms()

    # -- live updates ----------------------------------------------------------
    def _tick(self) -> None:
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        tr = self.explorer.tracker if self.explorer else None
        v = self.explorer.view if self.explorer else None
        if tr is None:
            self.l_screen.config(text="Explorer is off (Settings)")
        else:
            screen = "Room" if (v and v.in_room) else ("Changing room…" if "loading" in tr.state
                                                       else "Not a room view")
            self.l_screen.config(text=f"Currently viewing: {screen}   ·   {tr.state}")
            cur = tr.current
            if cur:
                known = self.atlas.room(cur["room_id"])
                kind = KIND.get(known["kind"] if known else None, "-")
                owner = (known or {}).get("owner") or "?"
                self.l_room.config(text=f"{cur['name']}   ·   ID {cur['room_id']}")
                visits = (known or {}).get("visits", 0)
                self.l_info.config(text=f"{kind} · owner {owner} · visits {visits}\n"
                                        f"Last change: {tr.last_change}")
            else:
                self.l_room.config(text="–")
                self.l_info.config(text="Open the share panel (¡Más amigos, más diversión!) so "
                                        "SK can read room IDs.")
        st = self.atlas.stats()
        self.l_stats.config(text=f"{st['rooms']} rooms known · {st['visited']} visited · "
                                 f"{st['walk']} walking connections · "
                                 f"{st['markers_linked']}/{st['markers']} map places linked")
        self._fill_pending()
        self.win.after(700, self._tick)

    def _fill_pending(self) -> None:
        items = self.atlas.pending()
        key = tuple(p["id"] for p in items)
        if key == self._pending_key:
            return
        self._pending_key = key
        for c in self.pend.winfo_children():
            c.destroy()
        if not items:
            tk.Label(self.pend, text="Nothing to check.", bg=PANEL, fg=DIM,
                     font=self.font(9)).pack(anchor="w")
            return
        for p in items:
            row = tk.Frame(self.pend, bg=PANEL)
            row.pack(fill="x", pady=2)
            frm = self.atlas.room(p["from_room"]) if p["from_room"] else None
            how = {"walk": "walked from", "taxi": "taxi from"}.get(p["method"], "")
            txt = f"{p['name']}  ·  ID {p['room_id']}"
            if frm:
                txt += f"\n{how} {frm['name']} ({frm['room_id']})"
            same = [r for r in self.atlas.by_name(p["name"]) if r["room_id"] != p["room_id"]]
            if same:
                txt += f"\nsame name as room {', '.join(str(r['room_id']) for r in same)}"
            tk.Label(row, text=txt, bg=PANEL, fg=WARN, font=self.font(9), anchor="w",
                     justify="left").pack(side="left", fill="x", expand=True)
            self._button(row, "Accept", lambda i=p["id"]: self._accept(i)).pack(side="left", padx=2)
            self._button(row, "Edit", lambda q=p: self._edit(q)).pack(side="left", padx=2)
            self._button(row, "Not this", lambda i=p["id"]: self._decline(i)).pack(side="left", padx=2)

    def _accept(self, pid: int) -> None:
        self.atlas.accept(pid)
        self._pending_key = None
        self._fill_rooms()

    def _decline(self, pid: int) -> None:
        self.atlas.decline(pid)
        self._pending_key = None

    def _edit(self, p: dict) -> None:
        rid = simpledialog.askinteger("Edit room", "Room ID (the number after habbosk.us/room/):",
                                      initialvalue=p["room_id"], parent=self.win, minvalue=1)
        if rid is None:
            return
        name = simpledialog.askstring("Edit room", "Room name:", initialvalue=p["name"],
                                      parent=self.win)
        if not name:
            return
        if not messagebox.askyesno("Edit room", f"Save this room?\n\nID {rid}\n{name}",
                                   parent=self.win):
            return
        if self.atlas.accept(p["id"], rid, name.strip()) == "duplicate":
            messagebox.showerror("Edit room", f"Room ID {rid} already belongs to another room.",
                                 parent=self.win)
            return
        self._pending_key = None
        self._fill_rooms()

    # -- room list ---------------------------------------------------------------
    def _fill_rooms(self) -> None:
        rooms = self.atlas.rooms(self.search.get().strip())
        self.rooms.delete(0, "end")
        self._room_ids = []
        for r in rooms:
            if r["status"] == "pending":
                continue
            mark = "●" if r["visits"] else "○"
            self.rooms.insert("end", f"{mark} {r['room_id']:>6}  {r['name']}")
            self._room_ids.append(r["room_id"])

    def _show_room(self) -> None:
        sel = self.rooms.curselection()
        if not sel:
            return
        r = self.atlas.room(self._room_ids[sel[0]])
        if r is None:
            return
        cons = self.atlas.connections(r["room_id"])
        out_ = [c for c in cons if c["from_room"] == r["room_id"]]
        in_ = [c for c in cons if c["to_room"] == r["room_id"]]

        def nm(i):
            x = self.atlas.room(i)
            return f"{x['name']} ({i})" if x else str(i)
        lines = [f"{r['name']}  ·  ID {r['room_id']}  ·  {KIND.get(r['kind'], r['kind'])}",
                 f"Owner: {r['owner'] or '?'} · visits: {r['visits']} · last: {r['last_visited'] or '-'}"]
        if r["aliases"]:
            lines.append("Earlier names: " + ", ".join(r["aliases"]))
        lines.append("Leads to (walking): " + (", ".join(nm(c["to_room"]) for c in out_
                                                         if c["method"] == "walk") or "not known yet"))
        lines.append("Reached from (walking): " + (", ".join(nm(c["from_room"]) for c in in_
                                                             if c["method"] == "walk") or "not known yet"))
        if r.get("note"):
            lines.append(r["note"])
        self.l_detail.config(text="\n".join(lines))

    def _export(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".json",
                                            initialfile="sk_world_atlas.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        c = self.atlas.export(path)
        messagebox.showinfo("World Atlas", f"Saved: {c['rooms']} rooms, {c['connections']} "
                            f"connections, {c['visits']} visits.\nSend this file to Claude to "
                            "add it to the world knowledge.", parent=self.win)


def _hide_from_capture(win) -> None:
    """Like the main window: invisible to screen capture, so SK never reads
    its own Explorer window as part of the game."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception:
        pass


def open_explorer(existing: Optional[ExplorerWindow], *args) -> ExplorerWindow:
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.lift()
                return existing
        except tk.TclError:
            pass
    return ExplorerWindow(*args)
