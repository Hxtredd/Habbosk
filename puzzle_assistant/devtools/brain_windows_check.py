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
  * the Brain's own automated tests (including the v1 -> v3 upgrade)
  * your real data files (quiz memory, World Atlas, Brain): opened
    READ-ONLY, each file's SHA-256 compared before and after, and the
    folder listing compared (proves nothing was written or created)

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


@check("Your real data files, read-only (nothing written)")
def real_files():
    base = os.environ.get("SK_ASSISTANT_DATA") or os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "SK Assistant")
    if not os.path.isdir(base):
        return None, f"no data folder at {base}"
    lines, ok = [], True
    listing = sorted(os.listdir(base))
    for name, table in (("habbosk.db", "questions"), ("atlas.db", None), ("knowledge.db", "claims")):
        p = os.path.join(base, name)
        if not os.path.exists(p):
            lines.append(f"{name}: not present")
            continue
        h0 = _sha(p)
        con = connect_readonly(p)
        try:
            quick = con.execute("PRAGMA quick_check").fetchone()[0]
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] \
                if table and table in tables else len(tables)
        finally:
            con.close()
        same = _sha(p) == h0
        ok = ok and same and quick == "ok"
        lines.append(f"{name}: readable, {'rows' if table else 'tables'} {n}, check {quick}, "
                     f"file unchanged: {same}")
    unchanged = sorted(os.listdir(base)) == listing
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
