"""Live Explorer / World Atlas window (Tk). Read-only: it only shows what SK
recognised and lets the owner accept or correct new rooms."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

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


class ExplorerPanel:
    """The Explorer's content, built inside any parent frame: a tab of the
    SK window, a docked side/top/bottom pane, or a separate window."""

    def __init__(self, parent, atlas, explorer, font):
        self.atlas, self.explorer, self.font = atlas, explorer, font
        from ..scrollframe import ScrollFrame
        self.scroll = ScrollFrame(parent, BG)
        self.win = self.scroll.outer                 # what the host places / destroys
        self.body = self.scroll.inner                # where the content goes
        self._build()
        self._tick()

    # -- layout ----------------------------------------------------------------
    def _section(self, title: str, grow: bool = False) -> tk.Frame:
        tk.Label(self.body, text=title.upper(), bg=BG, fg=DIM, font=self.font(8),
                 anchor="w").pack(fill="x", padx=14, pady=(10, 2))
        f = tk.Frame(self.body, bg=PANEL)
        f.pack(fill="both" if grow else "x", expand=grow, padx=12, pady=(0, 10 if grow else 0))
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
        f.bind("<Configure>", lambda e: [lb.config(wraplength=max(160, e.width - 30))
                                         for lb in (self.l_screen, self.l_room, self.l_info)])

        f = self._section("New rooms waiting for you")
        self.pend = tk.Frame(f, bg=PANEL)
        self.pend.pack(fill="x", padx=12, pady=8)

        f = self._section("World Atlas", grow=True)
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
        # Go to Room: one click = one ":taxi <ID>" in the hotel chat
        go = tk.Frame(f, bg=PANEL)
        go.pack(fill="x", padx=12, pady=(0, 4), before=lst)
        self.b_go = self._button(go, "Go to Room ▸", self._go_selected)
        self.b_go.pack(side="left")
        tk.Label(go, text="  or ID:", bg=PANEL, fg=DIM, font=self.font(9)).pack(side="left")
        self.e_id = tk.Entry(go, width=8, bg=HOVER, fg=FG, insertbackground=FG, relief="flat",
                             font=self.font(10))
        self.e_id.pack(side="left", padx=(4, 4))
        self.e_id.bind("<Return>", lambda e: self._go_typed())
        self._button(go, "Go", self._go_typed).pack(side="left")
        self.l_go = tk.Label(f, text="Select a room (or type an ID), then Go to Room.", bg=PANEL,
                             fg=DIM, font=self.font(9), anchor="w", justify="left")
        self.l_go.pack(fill="x", padx=12, pady=(0, 6), before=lst)
        f.bind("<Configure>", lambda e: self.l_go.config(wraplength=max(200, e.width - 30)),
               add="+")
        self.l_detail = tk.Label(f, bg=PANEL, fg=FG, font=self.font(9), anchor="w",
                                 justify="left", wraplength=500)
        self.l_detail.pack(fill="x", padx=12, pady=(0, 10), before=lst)
        f.bind("<Configure>", lambda e: self.l_detail.config(wraplength=max(200, e.width - 30)),
               add="+")
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
                self.l_room.config(text=f"{cur['name']}   ·   ID {cur['room_id'] or '?'}")
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
            if p["room_id"] is None:                 # a name SK doesn't know: ask for the ID
                frm = self.atlas.room(p["from_room"]) if p["from_room"] else None
                txt = f"{p['name']}  ·  ID unknown"
                if frm:
                    txt += f"\n{'walked' if p['method'] == 'walk' else 'came'} from {frm['name']}"
                tk.Label(row, text=txt, bg=PANEL, fg=WARN, font=self.font(9), anchor="w",
                         justify="left").pack(fill="x")
                btns = tk.Frame(row, bg=PANEL)
                btns.pack(fill="x", pady=(2, 4))
                self._button(btns, "Add ID", lambda q=p: self._add_id(q)).pack(side="left", padx=(0, 4))
                self._button(btns, "Not a room", lambda i=p["id"]: self._decline(i)).pack(side="left")
                continue
            frm = self.atlas.room(p["from_room"]) if p["from_room"] else None
            how = {"walk": "walked from", "taxi": "taxi from"}.get(p["method"], "")
            txt = f"{p['name']}  ·  ID {p['room_id']}"
            if frm:
                txt += f"\n{how} {frm['name']} ({frm['room_id']})"
            same = [r for r in self.atlas.by_name(p["name"]) if r["room_id"] != p["room_id"]]
            if same:
                txt += f"\nsame name as room {', '.join(str(r['room_id']) for r in same)}"
            tk.Label(row, text=txt, bg=PANEL, fg=WARN, font=self.font(9), anchor="w",
                     justify="left").pack(fill="x")
            btns = tk.Frame(row, bg=PANEL)
            btns.pack(fill="x", pady=(2, 4))
            self._button(btns, "Accept", lambda i=p["id"]: self._accept(i)).pack(side="left", padx=(0, 4))
            self._button(btns, "Edit", lambda q=p: self._edit(q)).pack(side="left", padx=(0, 4))
            self._button(btns, "Not this", lambda i=p["id"]: self._decline(i)).pack(side="left")

    def _accept(self, pid: int) -> None:
        self.atlas.accept(pid)
        self._pending_key = None
        self._fill_rooms()

    def _decline(self, pid: int) -> None:
        self.atlas.decline(pid)
        self._pending_key = None

    def _add_id(self, p: dict) -> None:
        rid = simpledialog.askinteger(
            "Room ID", f"Room ID of “{p['name']}”\n(the number after habbosk.us/room/ in the "
            "share panel):", parent=self.win, minvalue=1)
        if rid is None:
            return
        known = self.atlas.room(rid)
        if known and known["name"] != p["name"] and not messagebox.askyesno(
                "Room ID", f"ID {rid} is “{known['name']}”.\nIs “{p['name']}” its new name?",
                parent=self.win):
            return
        self.atlas.accept(p["id"], rid, p["name"])
        self._pending_key = None
        self._fill_rooms()

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

    # -- Go to Room ------------------------------------------------------------------
    def _go_selected(self) -> None:
        sel = self.rooms.curselection()
        if not sel:
            self._go_status("Select a room in the list first.", "fail")
            return
        self._go(self._room_ids[sel[0]])

    def _go_typed(self) -> None:
        txt = self.e_id.get().strip()
        if not txt.isdigit():
            self._go_status("Type the room ID (a number).", "fail")
            return
        self._go(int(txt))

    def _go(self, room_id: int) -> None:
        nav = getattr(self.explorer, "navigator", None) if self.explorer else None
        if nav is None:
            self._go_status("Go to Room is not available.", "fail")
            return
        r = self.atlas.room(room_id)
        self._go_status(f"Destination: {r['name'] if r else '?'}  ·  ID {room_id}", "info")
        nav.go(room_id, lambda text, kind: self.win.after(0, self._go_status, text, kind))

    def _go_status(self, text: str, kind: str) -> None:
        try:
            self.l_go.config(text=text, fg={"ok": GOOD, "fail": WARN}.get(kind, ACCENT))
        except tk.TclError:
            pass

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


def hide_from_capture(win) -> None:
    """Invisible to screen capture (like the main window), so SK never reads
    its own Explorer as part of the game."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception:
        pass
