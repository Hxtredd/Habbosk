"""Safety for the temporary Brain test bench (owner's requirements).

Three ways to open the Brain:
  * SANDBOX   - a disposable copy of the real knowledge.db (SQLite backup
                API: safe with an open database and WAL files). Everything,
                including test contributors, happens on the copy, which is
                deleted when the bench closes. The real file is never opened
                for writing.
  * REAL, read-only - only if knowledge.db exists and is already at the
                current schema version (so opening it writes nothing). No
                write is possible: WriteGuard refuses.
  * REAL, changes allowed - only after explicit confirmation. Before the
                first write, a timestamped backup is made with the SQLite
                backup API and checked (integrity + claim count). Existing
                backups are never overwritten. If the backup fails, every
                write is refused.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import sqlite3
import tempfile
from typing import Callable, Optional

from ..brain import Brain
from ..brain.schema import SCHEMA_VERSION
from ..brain.sqlite_ro import connect_readonly


class WritesDisabled(RuntimeError):
    """A write was attempted while changes are not allowed (nothing was written)."""


def _count_claims(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    except sqlite3.Error:
        return 0


def backup_path_for(db_path: str, now: Optional[_dt.datetime] = None) -> str:
    """knowledge.db.backup-YYYYMMDD-HHMMSS[-N] - never an existing name."""
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = f"{db_path}.backup-{stamp}"
    path, n = base, 1
    while os.path.exists(path):
        n += 1
        path = f"{base}-{n}"
    return path


def backup_database(db_path: str, now: Optional[_dt.datetime] = None) -> Optional[str]:
    """Copy db_path with SQLite's backup API to a new timestamped file and
    verify it. Returns the backup path (None if there is no database yet).
    Raises on any failure; a failed copy is removed (never a half backup)."""
    if not os.path.exists(db_path):
        return None
    dst = backup_path_for(db_path, now)
    src = connect_readonly(db_path)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok" or _count_claims(out) != _count_claims(src):
                raise OSError(f"backup check failed ({ok})")
        finally:
            out.close()
    except Exception:
        if os.path.exists(dst):
            os.remove(dst)
        raise
    finally:
        src.close()
    return dst


def schema_version_of(db_path: str) -> Optional[int]:
    """The schema version of an existing knowledge.db, read without writing."""
    if not os.path.exists(db_path):
        return None
    con = connect_readonly(db_path)
    try:
        r = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(r[0]) if r else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


class WriteGuard:
    """Every bench write goes through run_write(). Writes are refused until
    enable() succeeded, and enable() makes the backup first."""

    def __init__(self, db_path: str, allowed: bool = False,
                 backup: Optional[Callable[[str], Optional[str]]] = None):
        self.db_path = db_path
        self.enabled = allowed                 # sandbox: True from the start
        self.backup_file: Optional[str] = None
        self.error = ""
        self._backup = backup or (lambda path: backup_database(path))

    def enable(self) -> bool:
        """Make the backup (once), then allow writes. False if it failed."""
        if self.enabled:
            return True
        try:
            self.backup_file = self._backup(self.db_path)
        except Exception as exc:
            self.error = f"backup failed - changes stay disabled: {exc}"
            return False
        self.enabled = True
        return True

    def run_write(self, fn: Callable):
        if not self.enabled:
            raise WritesDisabled(self.error or "changes are not allowed in this mode")
        return fn()


class Session:
    """What the bench works on: a Brain plus its guard and mode."""

    def __init__(self, mode: str, real_path: str, quiz_path: str = "", atlas_path: str = ""):
        self.mode = mode                       # "sandbox" | "real_readonly" | "real_write"
        self.real_path = real_path
        self._tmp: Optional[str] = None
        if mode == "sandbox":
            self._tmp = tempfile.mkdtemp(prefix="sk_brain_sandbox_")
            path = os.path.join(self._tmp, "knowledge.db")
            if os.path.exists(real_path):       # copy the real knowledge (read-only access)
                src = connect_readonly(real_path)
                dst = sqlite3.connect(path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
            self.path = path
            self.guard = WriteGuard(path, allowed=True)
        else:
            self.path = real_path
            if mode == "real_readonly":
                v = schema_version_of(real_path)
                if v != SCHEMA_VERSION:
                    raise WritesDisabled(
                        "The real Brain database does not exist yet or needs an upgrade; "
                        "opening it would change it. Use the sandbox, or allow changes "
                        "(a backup is made first).")
                self.guard = WriteGuard(real_path, allowed=False)
            else:
                self.guard = WriteGuard(real_path, allowed=False)
                if not self.guard.enable():    # backup BEFORE the Brain even opens it
                    raise WritesDisabled(self.guard.error)
        self.brain = Brain(self.path, quiz_path, atlas_path)

    def close(self) -> None:
        self.brain.close()
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
