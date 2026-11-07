"""TEMPORARY SK Brain test bench (development tool, not the SK chat).

Launch:  BRAIN_TEST_BENCH.bat   or   python -m puzzle_assistant.devtools.brain_bench

It talks to the real SK Brain (knowledge.db in the SK data folder, plus the
quiz memory and World Atlas read-only) through the Brain API only, so all
Brain rules apply: new claims are unverified, privacy/isolation is kept,
nothing is verified without evidence and a review. Brain.ask() is still the
B1 placeholder: the bench shows what was FOUND and why, and never invents
an answer. Removing this file (and devtools/brain_checks.py, the .bat)
removes the bench; nothing else depends on it.
"""
from __future__ import annotations

import hashlib
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if __package__ in (None, ""):                           # run as a plain script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    __package__ = "puzzle_assistant.devtools"

from ..brain import Brain                               # noqa: E402
from ..brain.adapters import words                      # noqa: E402
from ..brain.model import STATUS_LABELS                 # noqa: E402
from ..brain.store import BrainError                    # noqa: E402
from .brain_checks import readonly_check, scenario_checks  # noqa: E402

BG, PANEL, FG, DIM = "#14161c", "#1d2029", "#e8eaf0", "#8b93a7"
GOOD, WARN, BAD, BLUE = "#34d399", "#fbbf24", "#f87171", "#60a5fa"
STATUS_COLOUR = {"verified": GOOD, "submitted": WARN, "awaiting_verification": WARN,
                 "conflicting": BAD, "rejected": DIM, "outdated": DIM}
FONT = ("Segoe UI", 10) if sys.platform == "win32" else ("DejaVu Sans", 10)


class Bench:
    def __init__(self, brain: Brain):
        self.b = brain
        self.jobs: "queue.Queue" = queue.Queue()
        self.root = r = tk.Tk()
        r.title("SK Brain – TEMPORARY test bench")
        r.geometry("1000x720")
        r.configure(bg=BG)
        st = ttk.Style(r)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TNotebook", background=BG)
        st.configure("TNotebook.Tab", padding=(12, 4))
        st.configure("Treeview", rowheight=22)
        self.viewers = {"Me (local user)": self.b.local_user}
        self._hits = []
        self._build()
        threading.Thread(target=self._worker, daemon=True).start()
        self._status()

    # -- background worker: Brain calls never block the window ----------------
    def _worker(self) -> None:
        while True:
            fn, done = self.jobs.get()
            try:
                res, err = fn(), None
            except Exception as exc:                # shown in the window, never a crash
                res, err = None, exc
            self.root.after(0, done, res, err)

    def run(self, fn, done) -> None:
        self.jobs.put((fn, done))

    # -- layout --------------------------------------------------------------------
    def _build(self) -> None:
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(top, text="TEMPORARY TEST BENCH – not the SK chat. Reasoning (B3) is not built: "
                 "SK shows what it FOUND, it does not answer.", bg=BG, fg=WARN, font=FONT,
                 anchor="w").pack(side="left")
        self.l_status = tk.Label(self.root, text="", bg=BG, fg=DIM, font=FONT, anchor="w",
                                 justify="left", wraplength=980)
        self.l_status.pack(fill="x", padx=10)
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._tab_ask(nb)
        self._tab_material(nb)
        self._tab_claims(nb)
        self._tab_checks(nb)

    def _frame(self, nb, title):
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text=title)
        return f

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, bg=PANEL, fg=FG, relief="flat",
                         activebackground="#2a2f3c", activeforeground=FG, font=FONT, padx=10)

    def _status(self) -> None:
        def get():
            st = self.b.stats()
            mods = []
            for s in self.b.sources:
                mods.append(f"{s.module}: {'connected (read-only)' if s.ro.db else 'not found'}")
            return st, mods

        def show(res, err):
            if err:
                self.l_status.config(text=f"● Brain error: {err}", fg=BAD)
                return
            st, mods = res
            self.l_status.config(
                text=f"● Connected: {self.b.store.path}  ·  your claims {st['claims']} "
                     f"(verified {st['verified']}, unverified {st['submitted'] + st['awaiting_verification']}, "
                     f"conflicting {st['conflicting']})  ·  sources {st['sources']}  ·  " + "  ·  ".join(mods),
                fg=GOOD)
        self.run(get, show)

    # -- Ask -----------------------------------------------------------------------
    def _tab_ask(self, nb) -> None:
        f = self._frame(nb, "Ask")
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=6)
        tk.Label(row, text="Ask as:", bg=BG, fg=DIM, font=FONT).pack(side="left")
        self.viewer = ttk.Combobox(row, values=list(self.viewers), state="readonly", width=26)
        self.viewer.current(0)
        self.viewer.pack(side="left", padx=4)
        self._btn(row, "+ test contributor", self._add_tester).pack(side="left", padx=4)
        self._btn(row, "Clear conversation", self._clear).pack(side="right")
        tk.Label(row, text="(only clears this view)", bg=BG, fg=DIM, font=FONT).pack(side="right",
                                                                                 padx=6)
        self.conv = tk.Text(f, height=10, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                            wrap="word", font=FONT, state="disabled")
        self.conv.pack(fill="both", expand=True, padx=6)
        for tag, col in (("you", BLUE), ("sk", FG), ("note", DIM), ("verified", GOOD),
                         ("unverified", WARN), ("conflict", BAD)):
            self.conv.tag_configure(tag, foreground=col)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=6)
        self.q = tk.Entry(row, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=FONT)
        self.q.pack(side="left", fill="x", expand=True, ipady=4)
        self.q.bind("<Return>", lambda e: self._send())
        self._btn(row, "Send", self._send).pack(side="left", padx=(6, 0))
        tk.Label(f, text="Records the Brain retrieved (click one to see why and from where):",
                 bg=BG, fg=DIM, font=FONT, anchor="w").pack(fill="x", padx=6)
        cols = ("status", "scope", "score", "module", "text", "source")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=7)
        for c, w in zip(cols, (150, 80, 55, 95, 380, 220)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        for st, col in STATUS_COLOUR.items():
            self.tree.tag_configure(st, foreground={"verified": "#047857", "conflicting": "#b91c1c",
                                                    "rejected": "#6b7280", "outdated": "#6b7280"}
                                    .get(st, "#92400e"))
        self.tree.pack(fill="x", padx=6)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._why())
        self.why = tk.Label(f, text="", bg=BG, fg=FG, font=FONT, anchor="w", justify="left",
                            wraplength=950)
        self.why.pack(fill="x", padx=6, pady=6)

    def _say(self, text, tag="sk") -> None:
        self.conv.config(state="normal")
        self.conv.insert("end", text + "\n", tag)
        self.conv.see("end")
        self.conv.config(state="disabled")

    def _clear(self) -> None:
        self.conv.config(state="normal")
        self.conv.delete("1.0", "end")
        self.conv.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.why.config(text="")

    def _add_tester(self) -> None:
        n = len(self.viewers)
        name = f"Test contributor {n}"
        cid = self.b.store.add_contributor(name, "external_user")
        self.viewers[name] = cid
        self.viewer.config(values=list(self.viewers))
        self.viewer.set(name)
        self._say(f"Added '{name}' (a separate person for isolation tests). Asking as them shows "
                  "only shared knowledge + their own claims.", "note")

    def _viewer_id(self) -> str:
        return self.viewers.get(self.viewer.get(), self.b.local_user)

    def _send(self) -> None:
        text = self.q.get().strip()
        if not text:
            return
        self.q.delete(0, "end")
        self._say(f"You ({self.viewer.get()}): {text}", "you")
        viewer = self._viewer_id()
        self.run(lambda: self.b.ask(text, viewer=viewer), lambda r, e: self._answer(text, r, e))

    def _answer(self, question, res, err) -> None:
        if err:
            self._say(f"SK: error - {err}", "conflict")
            return
        self.tree.delete(*self.tree.get_children())
        self.why.config(text="")
        self._hits = res.hits
        if not res.hits:
            self._say("SK: I found nothing in my knowledge about this. (No answer - I don't guess.)",
                      "sk")
        else:
            self._say(f"SK: I found {len(res.hits)} related record(s). I can't reason over them "
                      "yet (stage B3), so here is what I found - not an answer:", "sk")
            for h in res.hits[:5]:
                tag = "verified" if h.established else ("conflict" if h.status == "conflicting"
                                                        else "unverified")
                self._say(f"   • {h.display()}   ({h.module}, score {h.score:.2f})", tag)
        self._say("   " + " · ".join(res.trace), "note")
        for i, h in enumerate(res.hits):
            scope = h.detail.get("scope", "module")
            self.tree.insert("", "end", iid=str(i), tags=(h.status,),
                             values=(h.label, scope, f"{h.score:.2f}", h.module, h.text,
                                     "; ".join(h.sources)))
        self._last_q = question
        self._status()

    def _why(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        h = self._hits[int(sel[0])]
        q = getattr(self, "_last_q", "")
        shared = sorted(words(q) & words(f"{h.text} {h.category}"))
        lines = [f"Why: the question's words {', '.join(shared) or '(none)'} also appear in this "
                 f"record (B1 search = word overlap; score = share of your question's words found).",
                 f"Status: {h.label}" + ("" if h.established else
                                         "  - NOT an established fact"),
                 f"From: {h.module} · " + "; ".join(h.sources)]
        if h.module == "library":
            ex = self.b.explain(int(h.record_id), viewer=self._viewer_id())
            for e in ex["evidence"]:
                lines.append(f"Evidence: {e['kind']} – {e['title'] or 'no source'} "
                             f"({e['reference'] or ''}) {e['locator'] or ''}")
            for v in ex["versions"]:
                lines.append(f"Earlier wording v{v['version']} ({v['old_status']}): {v['text']}")
            for l in ex["links"]:
                lines.append(f"Link: claim {l['from_claim']} {l['kind']} claim {l['to_claim']}")
        self.why.config(text="\n".join(lines))

    # -- Add material ------------------------------------------------------------------
    def _tab_material(self, nb) -> None:
        f = self._frame(nb, "Add material")
        tk.Label(f, text="Paste a page / text (saved whole as a source; NOT searchable by itself - "
                 "add claims from it in the 'Claims' tab).", bg=BG, fg=WARN, font=FONT,
                 anchor="w").pack(fill="x", padx=6, pady=(6, 2))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=2)
        self.m_title = self._field(row, "Title", 30)
        self.m_ref = self._field(row, "Reference (URL / file)", 34)
        self.m_kind = ttk.Combobox(row, values=["guide_page", "document", "webpage", "screenshot",
                                                "video", "user_statement", "other"],
                                   state="readonly", width=14)
        self.m_kind.current(0)
        self.m_kind.pack(side="left", padx=4)
        self.m_private = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="private", variable=self.m_private, bg=BG, fg=FG,
                       selectcolor=PANEL, activebackground=BG, font=FONT).pack(side="left")
        self.m_text = tk.Text(f, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", wrap="word",
                              font=FONT)
        self.m_text.pack(fill="both", expand=True, padx=6, pady=4)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=(0, 6))
        self._btn(row, "Load text file…", self._load_file).pack(side="left")
        self._btn(row, "Save as source", self._save_source).pack(side="left", padx=6)
        self.m_msg = tk.Label(row, text="", bg=BG, fg=DIM, font=FONT, anchor="w")
        self.m_msg.pack(side="left", padx=6)

    def _field(self, parent, label, width):
        tk.Label(parent, text=label, bg=BG, fg=DIM, font=FONT).pack(side="left", padx=(4, 2))
        e = tk.Entry(parent, width=width, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                     font=FONT)
        e.pack(side="left", padx=(0, 6))
        return e

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, filetypes=[
            ("Text", "*.txt *.md *.html *.htm *.csv *.json"), ("All files", "*.*")])
        if not path:
            return
        data = open(path, "rb").read()
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        self.m_text.delete("1.0", "end")
        self.m_text.insert("1.0", text)
        if not self.m_title.get():
            self.m_title.insert(0, os.path.basename(path))
        if not self.m_ref.get():
            self.m_ref.insert(0, path)
        self.m_msg.config(text=f"Loaded {len(text)} characters (not saved yet).", fg=DIM)

    def _save_source(self) -> None:
        text = self.m_text.get("1.0", "end").strip()
        title = self.m_title.get().strip()
        if not text or not title:
            self.m_msg.config(text="A title and some text are needed.", fg=BAD)
            return
        kind, ref, private = self.m_kind.get(), self.m_ref.get().strip(), self.m_private.get()

        def save():
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            sid = self.b.add_source(kind, title=title, reference=ref, content_hash=h,
                                    private=private, raw_text=text)
            sec = self.b.add_section(sid, text, heading_path=title)
            return sid, sec

        def done(res, err):
            if err:
                self.m_msg.config(text=f"Not saved: {err}", fg=BAD)
                return
            self.m_msg.config(text=f"Saved ✓ source #{res[0]}, section #{res[1]} (original text "
                                   "kept). Now add claims from it in 'Claims'.", fg=GOOD)
            self._refresh_sources()
            self._status()
        self.run(save, done)

    # -- Claims ---------------------------------------------------------------------------
    def _tab_claims(self, nb) -> None:
        f = self._frame(nb, "Claims")
        tk.Label(f, text="Enter one statement at a time, linked to a saved source. New claims are "
                 "UNVERIFIED; 'verified' needs evidence + a review (Brain rules).", bg=BG, fg=WARN,
                 font=FONT, anchor="w").pack(fill="x", padx=6, pady=(6, 2))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=2)
        tk.Label(row, text="Source", bg=BG, fg=DIM, font=FONT).pack(side="left")
        self.c_source = ttk.Combobox(row, state="readonly", width=40)
        self.c_source.pack(side="left", padx=4)
        self.c_origin = ttk.Combobox(row, values=["imported", "user", "observed"], state="readonly",
                                     width=10)
        self.c_origin.current(0)
        self.c_origin.pack(side="left", padx=4)
        self.c_ev = ttk.Combobox(row, values=["direct (the source shows it)",
                                              "assertion (just stated)", "no evidence"],
                                 state="readonly", width=26)
        self.c_ev.current(0)
        self.c_ev.pack(side="left", padx=4)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=2)
        self.c_text = self._field(row, "Statement", 60)
        self.c_subject = self._field(row, "Subject", 16)
        self.c_cat = self._field(row, "Category", 12)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=2)
        self.c_loc = self._field(row, "Where in the source (quote / paragraph)", 40)
        self._btn(row, "Add claim", self._add_claim).pack(side="left", padx=6)
        self.c_msg = tk.Label(f, text="", bg=BG, fg=DIM, font=FONT, anchor="w")
        self.c_msg.pack(fill="x", padx=6)

        tk.Label(f, text="Your claims (select one to review / link / correct):", bg=BG, fg=DIM,
                 font=FONT, anchor="w").pack(fill="x", padx=6, pady=(8, 0))
        cols = ("id", "status", "subject", "text", "version")
        self.ctree = ttk.Treeview(f, columns=cols, show="headings", height=9)
        for c, w in zip(cols, (40, 190, 120, 520, 60)):
            self.ctree.heading(c, text=c.capitalize())
            self.ctree.column(c, width=w, anchor="w")
        self.ctree.pack(fill="both", expand=True, padx=6)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x", padx=6, pady=6)
        for label, st in (("Mark verified", "verified"), ("Reject", "rejected"),
                          ("Outdated", "outdated")):
            self._btn(row, label, lambda s=st: self._review(s)).pack(side="left", padx=2)
        self.c_other = self._field(row, "  contradicts claim id", 6)
        self._btn(row, "Link as contradiction", self._contradict).pack(side="left", padx=2)
        self.c_new = self._field(row, "  corrected text", 30)
        self._btn(row, "Correct", self._correct).pack(side="left", padx=2)
        self._refresh_sources()
        self._refresh_claims()

    def _refresh_sources(self) -> None:
        def get():
            with self.b.store._lock:
                rows = self.b.store._db.execute(
                    "SELECT s.id, s.title, MIN(x.id) AS section FROM sources s LEFT JOIN sections x "
                    "ON x.source_id = s.id WHERE s.added_by=? GROUP BY s.id ORDER BY s.id DESC",
                    (self.b.local_user,)).fetchall()
            return [(r["id"], r["title"], r["section"]) for r in rows]

        def show(res, err):
            if err:
                return
            self._sources = res
            self.c_source.config(values=[f"#{i} {t}" for i, t, _ in res])
            if res and not self.c_source.get():
                self.c_source.current(0)
        self.run(get, show)

    def _refresh_claims(self) -> None:
        def show(res, err):
            if err:
                return
            self.ctree.delete(*self.ctree.get_children())
            for c in res:
                self.ctree.insert("", "end", iid=str(c["id"]), values=(
                    c["id"], STATUS_LABELS.get(c["status"], c["status"]), c["subject"], c["text"],
                    c["version"]))
        self.run(lambda: self.b.store.claims(limit=2000), show)

    def _add_claim(self) -> None:
        text = self.c_text.get().strip()
        if not text:
            self.c_msg.config(text="Type the statement first.", fg=BAD)
            return
        idx = self.c_source.current()
        src = self._sources[idx] if getattr(self, "_sources", None) and idx >= 0 else None
        ev = self.c_ev.get().split()[0]
        origin, subject, cat, loc = (self.c_origin.get(), self.c_subject.get().strip(),
                                     self.c_cat.get().strip(), self.c_loc.get().strip())

        def add():
            cid = self.b.learn(text, origin, subject, cat, section_id=src[2] if src else None)
            if ev == "direct" and src:
                self.b.add_evidence(cid, "direct", src[0], loc)
            elif ev == "assertion":
                self.b.add_evidence(cid, "assertion", src[0] if src else None, loc)
            return cid

        def done(res, err):
            if err:
                self.c_msg.config(text=f"Not added: {err}", fg=BAD)
                return
            self.c_msg.config(text=f"Added claim #{res} - status: Unverified (not checked yet). "
                                   "Ask about it in the 'Ask' tab.", fg=GOOD)
            self.c_text.delete(0, "end")
            self._refresh_claims()
            self._status()
        self.run(add, done)

    def _selected_claim(self):
        sel = self.ctree.selection()
        if not sel:
            self.c_msg.config(text="Select a claim in the list first.", fg=BAD)
            return None
        return int(sel[0])

    def _brain_action(self, fn, ok_text) -> None:
        def done(res, err):
            if err:
                self.c_msg.config(text=f"Refused by the Brain: {err}", fg=BAD)
            else:
                self.c_msg.config(text=ok_text, fg=GOOD)
            self._refresh_claims()
            self._status()
        self.run(fn, done)

    def _review(self, status) -> None:
        cid = self._selected_claim()
        if cid is not None:
            self._brain_action(lambda: self.b.review(cid, status, note="test bench"),
                               f"Claim #{cid} is now: {STATUS_LABELS[status]}")

    def _contradict(self) -> None:
        cid = self._selected_claim()
        other = self.c_other.get().strip()
        if cid is None or not other.isdigit():
            self.c_msg.config(text="Select a claim and type the id of the claim it contradicts.",
                              fg=BAD)
            return
        self._brain_action(lambda: self.b.link(cid, int(other), "contradicts"),
                           f"Claims #{cid} and #{other} are marked as conflicting (both kept).")

    def _correct(self) -> None:
        cid = self._selected_claim()
        new = self.c_new.get().strip()
        if cid is None or not new:
            return
        self._brain_action(lambda: self.b.correct(cid, new, reason="test bench"),
                           f"Claim #{cid} corrected; the old wording is kept as a version.")

    # -- Checks ----------------------------------------------------------------------------
    def _tab_checks(self, nb) -> None:
        f = self._frame(nb, "Checks")
        tk.Label(f, text="Automatic checks. The scenario checks use a THROWAWAY database (your "
                 "knowledge is not touched); the read-only check reads your real quiz memory and "
                 "World Atlas and compares their files before and after.", bg=BG, fg=DIM,
                 font=FONT, anchor="w", justify="left", wraplength=950).pack(fill="x", padx=6,
                                                                             pady=6)
        self._btn(f, "Run all checks", self._run_checks).pack(anchor="w", padx=6)
        self.chk = tk.Text(f, bg=PANEL, fg=FG, relief="flat", wrap="word", font=FONT,
                           state="disabled")
        self.chk.pack(fill="both", expand=True, padx=6, pady=6)
        self.chk.tag_configure("pass", foreground=GOOD)
        self.chk.tag_configure("fail", foreground=BAD)
        self.chk.tag_configure("dim", foreground=DIM)
        manual = ("\nTry it yourself:\n"
                  " 1. Add material: paste a short text, give it a title, Save as source.\n"
                  " 2. Claims: pick that source, type one statement from it, Add claim.\n"
                  " 3. Ask: ask about it -> the claim appears, labelled Unverified, with its source.\n"
                  " 4. Claims: add an opposite statement, select it, type the first claim's id, "
                  "Link as contradiction -> both show as Conflicting.\n"
                  " 5. Ask something unrelated -> 'I found nothing'.\n"
                  " 6. Ask: '+ test contributor', ask as them -> your personal claims are not shown.\n"
                  " 7. Claims: 'Mark verified' on a claim with 'direct' evidence works; on one with "
                  "only an assertion the Brain refuses.\n")
        self._chk(manual, "dim")

    def _chk(self, text, tag=None) -> None:
        self.chk.config(state="normal")
        self.chk.insert("end", text + "\n", tag)
        self.chk.config(state="disabled")

    def _run_checks(self) -> None:
        self._chk("Running…", "dim")

        def done(res, err):
            if err:
                self._chk(f"Error: {err}", "fail")
                return
            for name, ok, detail in res:
                self._chk(f"{'PASS' if ok else 'FAIL'}  {name}  –  {detail}",
                          "pass" if ok else "fail")
        self.run(lambda: scenario_checks() + readonly_check(self.b), done)

    def mainloop(self) -> None:
        self.root.mainloop()


def main() -> None:
    try:
        brain = Brain()
    except BrainError as exc:
        messagebox.showerror("SK Brain", str(exc))
        return
    try:
        Bench(brain).mainloop()
    finally:
        brain.close()


if __name__ == "__main__":
    main()
