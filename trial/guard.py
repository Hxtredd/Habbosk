"""Isolation guard for the SK Assistant TRIAL (development build, not a release).

The trial must never touch the everyday SK Assistant's data folder
(%APPDATA%\\SK Assistant: habbosk.db, atlas.db, knowledge.db, config.json,
keys.bin, diagnostics). Two layers:

1. SK Assistant's own data-folder setting (SK_ASSISTANT_DATA) points every
   database, setting and log of the trial at trial_data\\ inside the trial
   folder; the launcher checks every path before the window opens.
2. This guard installs a Python audit hook (sys.addaudithook) that runs
   before every file open, SQLite connection and file-system change made by
   Python code in the trial. Anything inside the normal data folder is
   refused with PermissionError - with ONE exception: reading (never writing)
   keys.bin, so the trial can use the saved Gemini key without copying it.
   Refusals are counted and written to trial_data\\blocked_access.log.
   An audit hook cannot be removed once installed.

Standard library only.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
from typing import Iterable, List, Optional, Set

TRIAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIAL_DATA = os.path.join(TRIAL_ROOT, "trial_data")

_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
                | getattr(os, "O_EXCL", 0))
_WATCHED = {"open", "sqlite3.connect", "sqlite3.connect/handle"}


class IsolationError(RuntimeError):
    """The trial would not be separate from the normal SK Assistant: it must not start."""


def norm(path) -> str:
    p = os.fsdecode(path)
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def inside(path, folder) -> bool:
    p, f = norm(path), norm(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def normal_data_dirs(environ=None, platform: Optional[str] = None) -> List[str]:
    """Where the everyday SK Assistant keeps its data (same rule as
    puzzle_assistant.config.user_data_dir), plus SK_ASSISTANT_DATA if the PC
    sets it for the normal app."""
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    if platform == "win32":
        base = environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    dirs = [os.path.join(base, "SK Assistant")]
    env = environ.get("SK_ASSISTANT_DATA")
    if env and not inside(env, TRIAL_DATA):
        dirs.append(env)
    return dirs


def check_layout(protected: Iterable[str], trial_root: str = TRIAL_ROOT,
                 trial_data: str = TRIAL_DATA) -> None:
    for d in protected:
        if inside(trial_data, d) or inside(d, trial_root) or inside(trial_root, d):
            raise IsolationError(f"the trial folder and the normal SK data folder overlap ({d})")


def _read_only(args) -> bool:
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else None
    if isinstance(mode, str):
        return not any(c in mode for c in "wax+")
    if isinstance(flags, int):
        return not flags & _WRITE_FLAGS
    return False


class Guard:
    def __init__(self, protected: Iterable[str], allow_read: Iterable[str] = (),
                 log_path: Optional[str] = None):
        self.protected = [norm(d) for d in protected]
        self.allow_read: Set[str] = {norm(p) for p in allow_read}
        self.log_path = log_path
        self.blocked = 0
        self.allowed_reads = 0
        self._tl = threading.local()
        self.installed = False

    def is_protected(self, path) -> bool:
        try:
            p = norm(path)
        except Exception:
            return False
        return any(p == d or p.startswith(d.rstrip("\\/") + os.sep) for d in self.protected)

    def install(self) -> None:
        if not self.installed:
            sys.addaudithook(self._hook)
            self.installed = True

    def _hook(self, event: str, args) -> None:
        if event not in _WATCHED and not event.startswith(("os.", "shutil.")):
            return
        if getattr(self._tl, "busy", False):
            return
        self._tl.busy = True
        try:
            for a in args:
                if isinstance(a, int) or not isinstance(a, (str, bytes, os.PathLike)):
                    continue
                try:
                    s = os.fsdecode(a)
                except Exception:
                    continue
                if not s or s == ":memory:" or s.startswith("file::memory:"):
                    continue
                if s.startswith("file:"):                 # SQLite URI: file:<path>?...
                    s = s[5:].split("?", 1)[0]
                if not self.is_protected(s):
                    continue
                if event == "open" and norm(s) in self.allow_read and _read_only(args):
                    self.allowed_reads += 1
                    continue
                self.blocked += 1
                self._log(event, s)
                raise PermissionError(f"SK trial: access to the normal SK Assistant data was "
                                      f"blocked ({event}: {s})")
        finally:
            self._tl.busy = False

    def _log(self, event: str, path: str) -> None:
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{_dt.datetime.now().isoformat(timespec='seconds')}  {event}  {path}\n")
        except Exception:
            pass
