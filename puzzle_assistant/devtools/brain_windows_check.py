"""SK Brain - Windows check (development tool; changes nothing of yours).

Runs on the user's own PC what could only be simulated elsewhere:
  * Python / SQLite versions, FTS5 present or not
  * a Brain in a temporary folder with an awkward name (space, #, %, ', é):
    create, write, search (FTS5 and plain search must agree), close, reopen
  * read-only opening on Windows paths: right file read, nothing created
  * Windows file locking: every closed database can be deleted at once
    (Windows refuses to delete a file that is still open)
  * two programs writing at once; a write blocked by another program's
    read is rolled back and leaves nothing locked
  * FTS5 table breaking while running: plain search takes over
  * B6: UTF-16 / CRLF source files from an awkward path, exact quotations,
    a new version, a purge and a withdrawal checked by a raw byte scan while
    SK is still running; the B5.2 -> B6 library upgrade (a real B5.2
    library), also with the process killed between its two folder renames
  * the Brain's own automated tests (including the v1 -> v3 upgrade)
  * another program changing the database while a Brain has it cached
  * the whole application test suite (needs the app's packages: the .bat
    installs them into .venv-check inside THIS folder - never into your
    installed SK Assistant)
  * search speed and memory with 10,000 claims
  * your real quiz memory and World Atlas files: NEVER opened with SQLite.
    They are read through devtools/safe_snapshot.py (in memory, or a byte
    copy in a private, non-synced temporary folder that is deleted
    afterwards); each file and its -wal / -shm / -journal sidecars are
    fingerprinted (size, time, SHA-256) before and after, and the folder
    listing compared. Your Brain database (knowledge.db) is NOT read here:
    BRAIN_DB_AUDIT.bat audits it, with the same method.

Everything else happens in a temporary folder that is deleted at the end.
The report is printed and saved as brain_windows_check_report.txt next to
this program's folder; your user name in paths is replaced by %USERPROFILE%.
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain.adapters import ReadOnlyDB  # noqa: E402
from puzzle_assistant.brain.sqlite_ro import connect_readonly  # noqa: E402

AWKWARD = "SK check #1 José's 100% (data)"
results = []          # (name, passed: True/False/None for 'info', detail)


def _mask(text: str) -> str:
    home = os.path.expanduser("~")
    return str(text).replace(home, "%USERPROFILE%") if home and len(home) > 3 else str(text)


def check(name):
    def wrap(fn):
        def run():
            try:
                ok, detail = fn()
            except Exception as exc:                      # a check failing is a result too
                ok, detail = False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            results.append((name, ok, _mask(detail)))
        run.__name__ = fn.__name__
        CHECKS.append(run)
        return run
    return wrap


CHECKS = []


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def code_fingerprint() -> str:
    """SHA-256 over the Brain's source files: shows exactly which code ran."""
    h = hashlib.sha256()
    brain = os.path.join(HERE, "puzzle_assistant", "brain")
    for name in sorted(os.listdir(brain)):
        if name.endswith(".py"):
            with open(os.path.join(brain, name), "rb") as fh:
                h.update(name.encode() + b"\0" + fh.read().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


@check("Code tested")
def code():
    return None, f"Brain source fingerprint {code_fingerprint()} (compare with the report from Claude)"


@check("Environment")
def env():
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        fts = "available"
    except sqlite3.Error as exc:
        fts = f"NOT available ({exc}) - plain search is used, results are the same"
    return None, (f"{platform.platform()} | Python {platform.python_version()} "
                  f"{struct.calcsize('P') * 8}-bit | SQLite {sqlite3.sqlite_version} | FTS5 {fts}")


def _workdir():
    root = tempfile.mkdtemp(prefix="sk_brain_wincheck_")
    d = os.path.join(root, AWKWARD)
    os.makedirs(d)
    return root, d


@check("Create, write, search, close, reopen (awkward folder name)")
def lifecycle():
    root, d = _workdir()
    try:
        path = os.path.join(d, "knowledge.db")
        b = Brain(path, "", "")
        b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        b.learn("Marta is the caretaker of the Demo Garden.", "user", "Demo Garden")
        q = "room ID of the Demo Garden"
        a, f = [h.text for h in b.search(q)], [h.text for h in b.search(q, use_fts=False)]
        fts = b.store.fts
        b.close()
        b = Brain(path, "", "")
        n = len(b.store.claims())
        again = [h.text for h in b.search(q)]
        b.close()
        ok = bool(a) and a == f == again and n == 2
        return ok, f"FTS5 in use: {fts}; FTS5 == plain search: {a == f}; after reopen: {n} claims"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("Read-only opening: right file, nothing created")
def readonly():
    root, d = _workdir()
    try:
        path = os.path.join(d, "habbosk.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, text TEXT)")
        con.executemany("INSERT INTO questions (text) VALUES (?)", [("a",), ("b",), ("c",)])
        con.commit()
        con.close()
        before = (sorted(os.listdir(root)), sorted(os.listdir(d)), _sha(path))
        ro = ReadOnlyDB(path)
        n = len(ro.rows("SELECT * FROM questions"))
        ro.close()
        try:
            write = connect_readonly(path)
            write.execute("INSERT INTO questions (text) VALUES ('x')")
            write.close()
            refused = False
        except sqlite3.OperationalError:
            refused = True
        missing = os.path.join(d, "missing.db")
        try:
            connect_readonly(missing).close()
            created = os.path.exists(missing)
        except sqlite3.OperationalError:
            created = os.path.exists(missing)
        after = (sorted(os.listdir(root)), sorted(os.listdir(d)), _sha(path))
        ok = n == 3 and refused and not created and before == after
        return ok, (f"rows read {n}/3; write refused: {refused}; missing file created: {created}; "
                    f"folder and file unchanged: {before == after}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("Windows file locking: closed files can be deleted")
def deletable():
    root, d = _workdir()
    try:
        path = os.path.join(d, "knowledge.db")
        b = Brain(path, "", "")
        b.learn("x", "user")
        b.search("x")
        b.close()
        os.remove(path)                                  # fails on Windows if still open
        ro_path = os.path.join(d, "ro.db")
        sqlite3.connect(ro_path).close()
        ro = ReadOnlyDB(ro_path)
        ro.close()
        os.remove(ro_path)
        from unittest import mock
        from puzzle_assistant.brain.store import KnowledgeStore
        bad = os.path.join(d, "bad.db")
        with mock.patch.object(KnowledgeStore, "_migrate",
                               side_effect=sqlite3.OperationalError("simulated failure")):
            try:
                KnowledgeStore(bad)
            except sqlite3.OperationalError:
                pass
        os.remove(bad)
        return True, "Brain, read-only file and a failed open all released their files"
    finally:
        shutil.rmtree(root, ignore_errors=True)


_WRITER = r'''
import sys; sys.path.insert(0, sys.argv[3])
from puzzle_assistant.brain import Brain
b = Brain(sys.argv[1], "", ""); errs = 0
for i in range(100):
    try: b.learn(f"Demo place {sys.argv[2]}{i} has room ID {i}.", "user")
    except Exception: errs += 1
b.close(); print(errs)
'''


@check("Two programs writing at the same time")
def concurrent():
    root, d = _workdir()
    try:
        path = os.path.join(d, "knowledge.db")
        Brain(path, "", "").close()
        procs = [subprocess.Popen([sys.executable, "-c", _WRITER, path, tag, HERE],
                                  stdout=subprocess.PIPE, text=True) for tag in "AB"]
        errs = [int((p.communicate(timeout=120)[0] or "0").strip() or 0) for p in procs]
        con = sqlite3.connect(path)
        n = con.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        idx = con.execute("SELECT COUNT(*) FROM claim_index").fetchone()[0]
        integ = con.execute("PRAGMA integrity_check").fetchone()[0]
        con.close()
        ok = errs == [0, 0] and n == idx == 200 and integ == "ok"
        return ok, f"claims {n}/200, index {idx}, errors {errs}, integrity {integ}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("A write blocked by another program is rolled back")
def blocked():
    root, d = _workdir()
    try:
        path = os.path.join(d, "knowledge.db")
        b = Brain(path, "", "")
        c = b.learn("The Demo Garden has room ID 900001.", "user")
        b.store._db.execute("PRAGMA busy_timeout = 300")
        reader = sqlite3.connect(path, timeout=0.3)
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM claims").fetchall()
        try:
            b.review(c, "rejected")
            raised = False
        except sqlite3.OperationalError:
            raised = True
        reader.rollback()
        reader.close()
        other = sqlite3.connect(path, timeout=0.3)
        other.execute("SELECT COUNT(*) FROM claims").fetchone()
        b.learn("later", "user")
        status = other.execute("SELECT status FROM claims WHERE id=?", (c,)).fetchone()[0]
        other.close()
        b.close()
        ok = raised and status == "submitted"
        return ok, f"blocked write reported: {raised}; status afterwards: {status} (must stay submitted)"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("FTS5 breaking while running")
def fts_break():
    root, d = _workdir()
    try:
        path = os.path.join(d, "knowledge.db")
        b = Brain(path, "", "")
        if not b.store.fts:
            b.close()
            return None, "FTS5 not available on this PC: plain search is used throughout"
        b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        want = [h.text for h in b.search("room ID of the Demo Garden")]
        b.store._db.execute("DROP TABLE claim_fts")
        b.store._db.commit()
        got = [h.text for h in b.search("room ID of the Demo Garden")]
        b.learn("The Demo Plaza has room ID 900002.", "user", "Demo Plaza")
        b.close()
        b = Brain(path, "", "")
        rebuilt = b.store._db.execute("SELECT COUNT(*) FROM claim_fts").fetchone()[0]
        b.close()
        ok = want == got and rebuilt == 2
        return ok, f"same results after the break: {want == got}; rebuilt on restart: {rebuilt}/2"
    finally:
        shutil.rmtree(root, ignore_errors=True)


_EXTERNAL = r'''
import sqlite3, sys; sys.path.insert(0, sys.argv[3])
from puzzle_assistant.brain import Brain
path, cid = sys.argv[1], int(sys.argv[2])
con = sqlite3.connect(path)            # a raw edit, as another tool would make it
con.execute("UPDATE claims SET status='outdated' WHERE id=?", (cid,))
con.commit(); con.close()
b = Brain(path, "", ""); b.learn("The Demo Plaza has room ID 900002.", "user", "Demo Plaza"); b.close()
'''


@check("Another program changes the database while it is cached")
def external_change():
    root, d = _workdir()
    try:
        path = os.path.join(d, "knowledge.db")
        b = Brain(path, "", "")
        c = b.learn("The Demo Garden has room ID 900001.", "user", "Demo Garden")
        b.search("room ID of the Demo Garden")                 # now cached
        subprocess.run([sys.executable, "-c", _EXTERNAL, path, str(c), HERE], check=True,
                       timeout=120)
        status = [h.status for h in b.search("room ID of the Demo Garden") if h.record_id == str(c)]
        plaza = [h.text for h in b.search("room ID of the Demo Plaza")
                 if h.text == "The Demo Plaza has room ID 900002."]
        b.close()
        ok = status == ["outdated"] and bool(plaza)
        return ok, f"status after the other program's edit: {status} (must be outdated); " \
                   f"its new claim found: {bool(plaza)}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("B5.2 separate personal / shared files (keys, sharing, withdrawal, migration)")
def separated_storage():
    """On this PC, in a temporary folder with an awkward name: the keys kept with
    Windows protection (DPAPI), a share and its withdrawal (the withdrawn text
    is then absent from every shared file - raw byte scan - and still complete
    in its owner's file), and a migration whose commit is a folder rename."""
    from puzzle_assistant.brain import keystore
    from puzzle_assistant.brain.library import Library, mint_grant
    from puzzle_assistant.brain.split import fingerprint, split_file, verify_library
    root, d = _workdir()
    try:
        kp = os.path.join(d, "keys", "host.key")
        hk = keystore.load_or_create_key(kp)
        with open(kp, "rb") as fh:
            blob = fh.read()
        dpapi = blob.startswith(keystore.MAGIC_DPAPI)
        same = keystore.load_or_create_key(kp) == hk
        if sys.platform == "win32":                       # on Windows: never stored in plain
            same = same and dpapi and hk not in blob
        lk = keystore.load_or_create_key(os.path.join(d, "keys", "library.key"))
        lib = Library(os.path.join(d, "brain"), hk, lk)
        g = lambda u, r="user": mint_grant(hk, lib.installation_id, lib.epoch, u, r)  # noqa: E731
        ana, luis = lib.create_user("Ana"), lib.create_user("Luis", "external_user")
        mod = lib.create_user("Mod", "moderator")
        s = lib.open_session(g(ana))
        src = s.add_source("document", title="WINCHECK-TITLE-MARK", content_hash="wc1")
        c = s.learn("Mira maintains the Demo Garden.", "imported", "Demo Garden")
        s.add_evidence(c, "direct", src, "Mira maintains the Demo Garden.")
        tok = s.propose(c, {src: ["quote", "details"]})
        s.close()
        m = lib.open_session(g(mod, "moderator"))
        cid = m.approve(m.proposals()[0]["file"], tok)
        m.review(cid, "verified")
        m.close()
        l2 = lib.open_session(g(luis))
        shared_ok = l2.respond("Who maintains the Demo Garden?").value == "Mira"
        l2.close()
        s = lib.open_session(g(ana))
        state = s.withdraw_source(src)
        s.close()
        l2 = lib.open_session(g(luis))
        withdrawn = l2.respond("Who maintains the Demo Garden?").reason == "evidence_withdrawn"
        l2.close()
        lib.close()
        hits = []
        for dd, _, names in os.walk(os.path.join(d, "brain")):
            for n in names:
                with open(os.path.join(dd, n), "rb") as fh:
                    if b"WINCHECK-TITLE-MARK" in fh.read():
                        hits.append(os.path.relpath(os.path.join(dd, n), d))
        kept_by_owner = bool(hits) and all(h.startswith(os.path.join("brain", "personal"))
                                           for h in hits)
        # migration of a single file: original untouched, commit by folder rename
        md = os.path.join(d, "migrate")
        os.makedirs(md)
        old = os.path.join(md, "knowledge.db")
        b = Brain(old, "", "")
        b.learn("The Demo Plaza has room ID 900002.", "user", "Demo Plaza")
        b.close()
        before = fingerprint(old)
        split_file(old, md, hk, lk)
        migrated = verify_library(os.path.join(md, "brain"), hk) == "verified" and \
            fingerprint(old) == before
        ok = all((same, shared_ok, state == "complete", withdrawn, kept_by_owner, migrated))
        return ok, (f"keys protected by Windows (DPAPI): {dpapi}; key reloads: {same}; shared "
                    f"answer: {shared_ok}; withdrawal: {state}, answer then 'evidence withdrawn': "
                    f"{withdrawn}; withdrawn text left only in its owner's file: {kept_by_owner}; "
                    f"migration (original unchanged, folder renamed, verified): {migrated}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _scan(top, marker: bytes):
    hits = []
    for dd, _, names in os.walk(top):
        for n in names:
            p = os.path.join(dd, n)
            with open(p, "rb") as fh:
                if marker in fh.read():
                    hits.append(os.path.relpath(p, top).replace(os.sep, "/"))
    return hits


@check("B6 imported sources: UTF-16 / CRLF files, exact quotes, purge, withdrawal (Windows files)")
def b6_sources():
    """Windows-only behaviour B6 relies on: files with CRLF line endings and a
    UTF-16 byte-order mark read from an awkward path, an exact quotation, a new
    version, a purge and a withdrawal whose text is then absent from every SK
    file (raw byte scan, with SK still running), and closed library files that
    Windows lets us delete at once."""
    from puzzle_assistant.brain.library import Library, mint_grant
    root, d = _workdir()
    try:
        hk, lk = os.urandom(32), os.urandom(32)
        lib = Library(os.path.join(d, "brain"), hk, lk)
        g = lambda u, r="user": mint_grant(hk, lib.installation_id, lib.epoch, u, r)  # noqa: E731
        ana, mod = lib.create_user("Ana"), lib.create_user("Mod", "moderator")
        src_dir = os.path.join(d, "Mis documentos (José)")
        os.makedirs(src_dir)
        p16 = os.path.join(src_dir, "guía #1.txt")
        with open(p16, "wb") as fh:
            fh.write("Mira maintains the Demo Garden.\r\n\r\nWINB6PURGEMARK stays a while."
                     "\r\n".encode("utf-16"))                       # BOM + CRLF
        s = lib.open_session(g(ana))
        r = s.import_file(p16, author_kind="named", author_name="Ada", publisher="Demo Press")
        sid = r["source_id"]
        c = s.learn("Mira maintains the Demo Garden.", "imported", "Demo Garden")
        cited = s.cite(c, sid, "Mira maintains the Demo Garden.")["supports"]
        documented = s.respond("Who maintains the Demo Garden?").category == "documented"
        with open(p16, "wb") as fh:
            fh.write("Mira maintains the Demo Garden.\r\n".encode("utf-16"))
        new_version = s.reimport(sid)["state"] == "new_version"
        changed = s.respond("Who maintains the Demo Garden?").category != "documented"
        purge = s.purge_version(sid, 1)["state"]
        purged_live = _scan(os.path.join(d, "brain"), b"WINB6PURGEMARK") == [] and \
            _scan(os.path.join(d, "brain"), "WINB6PURGEMARK".encode("utf-16-le")) == []
        s.cite(c, sid, "Mira maintains the Demo Garden.")
        p2 = os.path.join(src_dir, "shared notes.md")
        with open(p2, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("# Notes\n\nMira maintains the Demo Garden.\n\nWINB6SHAREDMARK is here.\n")
        sid2 = s.import_file(p2, author_kind="named", author_name="Ada", publisher="Other")["source_id"]
        s.cite(c, sid2, "Mira maintains the Demo Garden.")
        tok = s.propose(c, {sid2: ["quote", "full_text"]})
        m = lib.open_session(g(mod, "moderator"))
        m.approve(m.proposals()[0]["file"], tok)
        in_shared = any(h.startswith("shared") for h in
                        _scan(os.path.join(d, "brain"), b"WINB6SHAREDMARK"))
        state = s.withdraw_source(sid2)
        left = _scan(os.path.join(d, "brain"), b"WINB6SHAREDMARK")
        only_owner = bool(left) and all(h.startswith("personal/") for h in left)
        m.close()
        s.close()
        lib.close()
        locked = []
        for dd, _, names in os.walk(os.path.join(d, "brain")):
            for n in names:
                try:
                    os.remove(os.path.join(dd, n))
                except OSError as exc:
                    locked.append(f"{n}: {exc}")
        ok = all((cited, documented, new_version, changed, purge == "purged", purged_live,
                  in_shared, state == "complete", only_owner, not locked))
        return ok, (f"UTF-16 + CRLF import from an awkward path, exact quote supports: {cited}; "
                    f"documented: {documented}; new version: {new_version}, old quote no longer "
                    f"documents: {changed}; purge: {purge}, purged text absent while running: "
                    f"{purged_live}; full text shared: {in_shared}; withdrawal: {state}, text "
                    f"left only in its owner's personal file: {only_owner}; closed files "
                    f"deletable at once: {not locked}" + (f" {locked[:3]}" if locked else ""))
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("B6 PDF import: separate reading process, pages, refusals, hidden text (Windows)")
def b6_pdf():
    """What only Windows can show for PDFs: the reading process starts (python -I,
    no console window), pypdf is the pinned version, a PDF from an awkward path
    imports with its page numbers, encrypted / scanned / damaged PDFs are refused
    with nothing stored, a page with hidden text is reference only, a hanging
    reader is stopped by the time limit, and closed files can be deleted."""
    import io
    sys.path.insert(0, os.path.join(HERE, "tests"))
    try:
        import b6_pdf_maker as M
    except ImportError:
        return None, "tests/b6_pdf_maker.py not found (use the full download)"
    try:
        import pypdf
    except ImportError:
        return False, ("pypdf is not installed - BRAIN_WINDOWS_CHECK.bat installs it from "
                       "requirements-brain.txt (hash-checked)")
    from puzzle_assistant.brain import pdf_import
    from puzzle_assistant.brain.importer import ImportRefused
    from puzzle_assistant.brain.library import Library, mint_grant
    root, d = _workdir()
    try:
        version_ok = pypdf.__version__ == "6.21.4"
        hk, lk = os.urandom(32), os.urandom(32)
        lib = Library(os.path.join(d, "brain"), hk, lk)
        ana = lib.create_user("Ana")
        s = lib.open_session(mint_grant(hk, lib.installation_id, lib.epoch, ana, "user"))
        folder = os.path.join(d, "Mis PDF (José) #2")
        os.makedirs(folder)
        good = M.make([M.Page([M.text(72, 700, "Intro page.")]),
                       M.Page([M.text(72, 700, "Mira maintains the Demo Garden.")]),
                       M.Page([M.text(72, 700, "Kai maintains the Demo Garden."),
                               M.text(72, 680, "Hidden words.", pre="3 Tr")])])
        p = os.path.join(folder, "guía 1.pdf")
        with open(p, "wb") as fh:
            fh.write(good)
        t0 = time.time()
        sid = s.import_file(p, author_kind="named", author_name="Ada")["source_id"]
        took = time.time() - t0
        c = s.learn("Mira maintains the Demo Garden.", "imported", "Demo Garden")
        r = s.cite(c, sid, "Mira maintains the Demo Garden.")
        page_ok = r["supports"] and r.get("page") == 2
        c2 = s.learn("Kai maintains the Demo Garden.", "imported", "Demo Garden")
        hidden_ref = s.cite(c2, sid, "Kai maintains the Demo Garden.")["supports"] is False
        before = s.store._db.execute("SELECT COUNT(*) FROM main.sources").fetchone()[0]
        refused = []
        w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(good)))
        w.encrypt("", "owner", algorithm="RC4-128")
        enc = io.BytesIO()
        w.write(enc)
        for name, data in (("encrypted.pdf", enc.getvalue()),
                           ("scan.pdf", M.make([M.Page([M.image(0, 0, 612, 792)])])),
                           ("damaged.pdf", good[:len(good) // 2])):
            q = os.path.join(folder, name)
            with open(q, "wb") as fh:
                fh.write(data)
            try:
                s.import_file(q, author_kind="unknown")
                refused.append(f"{name}: NOT refused")
            except ImportRefused:
                pass
        nothing = s.store._db.execute("SELECT COUNT(*) FROM main.sources").fetchone()[0] == before
        old = pdf_import._BOOT
        pdf_import._BOOT = "import time; time.sleep(30)"
        try:
            t1 = time.time()
            pdf_import.parse_pdf(good, timeout=3)
            stopped = False
        except ImportRefused:
            stopped = time.time() - t1 < 15
        finally:
            pdf_import._BOOT = old
        s.close()
        lib.close()
        locked = []
        for dd, _, names in os.walk(os.path.join(d, "brain")):
            for n in names:
                try:
                    os.remove(os.path.join(dd, n))
                except OSError as exc:
                    locked.append(f"{n}: {exc}")
        ok = all((version_ok, page_ok, hidden_ref, not refused, nothing, stopped, not locked))
        return ok, (f"pypdf {pypdf.__version__} (expected 6.21.4): {version_ok}; import from an "
                    f"awkward path in {took:.1f} s, quote supports with page 2: {page_ok}; page "
                    f"with hidden text reference only: {hidden_ref}; encrypted / scanned / "
                    f"damaged refused: {not refused} {refused}; nothing stored: {nothing}; "
                    f"hanging reader stopped by the time limit: {stopped}; closed files "
                    f"deletable: {not locked}" + (f" {locked[:3]}" if locked else ""))
    finally:
        shutil.rmtree(root, ignore_errors=True)


@check("B6 library upgrade: copy, two folder renames, power loss between them (Windows renames)")
def b6_upgrade():
    """A real B5.2 library (tests/fixtures/b52_library.zip, written by the B5.2
    code) upgraded in an awkward folder; then again with the process killed
    between the two folder renames, recovered, answers compared."""
    import json
    import zipfile
    from puzzle_assistant.brain import upgrade as UP
    from puzzle_assistant.brain.library import Library, mint_grant
    fixture = os.path.join(HERE, "tests", "fixtures", "b52_library.zip")
    child = os.path.join(HERE, "tests", "b6_child.py")
    if not os.path.isfile(fixture):
        return None, "tests/fixtures/b52_library.zip not found (use the full download)"
    lines, ok = [], True
    for crash in ("", "between_renames", "after_swapping"):
        root, d = _workdir()
        try:
            with zipfile.ZipFile(fixture) as z:
                z.extractall(d)
            with open(os.path.join(d, "fixture.json"), encoding="utf-8") as fh:
                info = json.load(fh)
            os.remove(os.path.join(d, "fixture.json"))
            hk, lk = bytes.fromhex(info["hk"]), bytes.fromhex(info["lk"])
            before = UP.tree_fingerprint(os.path.join(d, "brain"))
            if crash:
                r = subprocess.run([sys.executable, child, "upgrade", d, hk.hex()], cwd=HERE,
                                   env=dict(os.environ, SK_TEST_CRASH_AT=crash),
                                   capture_output=True, text=True, timeout=600)
                rep = UP.recover_upgrade(d, hk)
                step = f"killed at {crash} (exit {r.returncode}), recovery: " + \
                    "; ".join(x["action"] for x in rep["recovered"]) + \
                    ("; REPORT ONLY" if rep["report_only"] else "")
                ok = ok and r.returncode == 77 and not rep["report_only"]
                if UP.tree_fingerprint(os.path.join(d, "brain")) == before:
                    UP.upgrade_library(d, hk)
            else:
                rep = UP.upgrade_library(d, hk)
                step = "upgraded; old library and backup unchanged: " + \
                    str(UP.tree_fingerprint(rep["old_library"]) == before ==
                        UP.tree_fingerprint(rep["backup"]))
                ok = ok and UP.tree_fingerprint(rep["old_library"]) == before
            lib = Library(os.path.join(d, "brain"), hk, lk)
            same = True
            for who, ans in info["answers"].items():
                s = lib.open_session(mint_grant(hk, lib.installation_id, lib.epoch,
                                                info["ids"][who], "user"))
                for q, want in ans.items():
                    a = s.respond(q)
                    same = same and [a.category, a.reason, a.value] == want
                s.close()
            lib.close()
            ok = ok and same
            lines.append(f"{step}; every answer as the B5.2 code gave it: {same}")
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return ok, "\n".join(lines)


@check("Brain automated tests on this PC")
def unit_tests():
    loader = unittest.TestLoader()
    tests_dir = os.path.join(HERE, "tests")
    if not os.path.isdir(tests_dir):
        return None, "tests folder not found (use the full download of the branch)"
    sys.path.insert(0, tests_dir)
    suite = unittest.TestSuite()
    for pattern in ("test_brain*.py", "test_bench_safety.py"):
        suite.addTests(loader.discover(tests_dir, pattern=pattern, top_level_dir=tests_dir))
    out = io.StringIO()
    r = unittest.TextTestRunner(stream=out, verbosity=1).run(suite)
    detail = (f"ran {r.testsRun}: passed {r.testsRun - len(r.failures) - len(r.errors) - len(r.skipped)}"
              f", failed {len(r.failures)}, errors {len(r.errors)}, skipped {len(r.skipped)}")
    if r.failures or r.errors:
        detail += "\n" + "\n".join(f"--- {t}\n{tb}" for t, tb in (r.failures + r.errors)[:10])
    return r.wasSuccessful(), detail


@check("Whole application test suite")
def full_suite():
    tests_dir = os.path.join(HERE, "tests")
    if not os.path.isdir(tests_dir):
        return None, "tests folder not found (use the full download of the branch)"
    # the app's tests run with their data folder pointed at a throwaway temp folder,
    # so even a faulty test could never reach your real %APPDATA%\SK Assistant
    sandbox = tempfile.mkdtemp(prefix="sk_suite_data_")
    env = dict(os.environ, SK_ASSISTANT_DATA=sandbox)
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=HERE,
                           capture_output=True, text=True, timeout=1800, env=env)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    out = r.stderr + r.stdout
    tail = [l for l in out.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
    missing = sorted({l.split("'")[1] for l in out.splitlines()
                      if "No module named" in l and "'" in l})
    detail = " | ".join(tail[-2:]) or "no summary"
    if missing:
        detail += f"\nmissing packages (run BRAIN_WINDOWS_CHECK.bat, which installs them): {missing}"
    if r.returncode:
        bad = [l for l in out.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        detail += "\n" + "\n".join(bad[:20])
    return r.returncode == 0, detail


@check("Search speed and memory (10,000 claims)")
def speed_memory():
    sys.path.insert(0, os.path.join(HERE, "tests"))
    import brain_memory
    return None, brain_memory.describe(brain_memory.measure(10000))


@check("Your real quiz memory and World Atlas, read safely (nothing written)")
def real_files():
    from puzzle_assistant.devtools import knowledge_audit, safe_snapshot
    base = os.environ.get("SK_ASSISTANT_DATA") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "SK Assistant")
    if not os.path.isdir(base):
        return None, f"no data folder at {base}"
    if knowledge_audit.sk_is_running():
        return None, "SK Assistant is running - close it to include this check (skipped, nothing read)"
    lines, ok = [], True
    listing = safe_snapshot.listing(base)
    for name, table in (("habbosk.db", "questions"), ("atlas.db", None)):
        p = os.path.join(base, name)
        if not os.path.exists(p):
            lines.append(f"{name}: not present")
            continue
        before = safe_snapshot.fingerprint(p)
        s = safe_snapshot.open_snapshot(p)          # never the original: memory or a temp copy
        try:
            quick = s.con.execute("PRAGMA quick_check").fetchone()[0]
            tables = [r[0] for r in s.con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            n = s.con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] \
                if table and table in tables else len(tables)
        finally:
            s.close()
        same = safe_snapshot.fingerprint(p) == before
        temp_ok = s.temp_dir is None or s.temp_removed
        ok = ok and same and quick == "ok" and temp_ok
        lines.append(f"{name}: readable ({s.method}), {'rows' if table else 'tables'} {n}, check {quick}, "
                     f"file and sidecars unchanged: {same}"
                     + ("" if s.temp_dir is None else f", temporary copy deleted: {s.temp_removed}"))
    lines.append("knowledge.db: not read by this check (use BRAIN_DB_AUDIT.bat)")
    unchanged = safe_snapshot.listing(base) == listing
    ok = ok and unchanged
    lines.append(f"no file created or removed in the folder: {unchanged}")
    return ok, f"{base}\n  " + "\n  ".join(lines)


def main() -> int:
    t0 = time.time()
    for run in CHECKS:
        print(f"... {run.__name__}", flush=True)
        run()
    lines = ["SK Brain - Windows check", "=" * 60]
    for name, ok, detail in results:
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[ok]
        lines.append(f"[{mark}] {name}\n       " + detail.replace("\n", "\n       "))
    failed = sum(1 for _, ok, _ in results if ok is False)
    lines.append("=" * 60)
    lines.append(f"{sum(1 for _, ok, _ in results if ok)} passed, {failed} failed, "
                 f"{sum(1 for _, ok, _ in results if ok is None)} info  ({time.time() - t0:.0f} s)")
    text = "\n".join(lines)
    print("\n" + text)
    try:
        with open(os.path.join(HERE, "brain_windows_check_report.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("\nSaved: brain_windows_check_report.txt")
    except OSError as exc:
        print(f"(could not save the report: {exc})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
