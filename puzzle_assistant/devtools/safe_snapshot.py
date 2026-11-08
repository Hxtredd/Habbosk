"""Read a SQLite database WITHOUT ever opening the original with SQLite.

Opening a database with SQLite - even read-only - can roll back a leftover
journal, replay or checkpoint a write-ahead log (-wal), or create a -shm
file. This module never does that to the original:

  1. it records size, modification time and SHA-256 of the database and of
     every sidecar file (-wal, -shm, -journal);
  2. if no -wal / -journal holds data, it reads the database file into
     MEMORY and opens that image (Python 3.11+: sqlite3 "deserialize") -
     no copy is written to disk;
  3. otherwise (or on an older Python) it copies the database and its
     -wal / -journal byte for byte into a private temporary folder and opens
     only the COPY, so a leftover journal or log is applied to the copy.
     The temporary folder:
       * is under %LOCALAPPDATA%\\Temp on Windows (the system temp folder
         elsewhere), or a folder the user chooses;
       * is refused if it is inside a cloud-synced folder (OneDrive,
         Dropbox, Google Drive, iCloud, Box...), on a network drive or on
         removable media;
       * gets a random name, and is readable by the user only (Windows
         profile temp folders are private to the user by default; 0700 on
         other systems);
       * is deleted as soon as the snapshot is closed, also after an error;
         a leftover from an interrupted run is removed the next time.
     Deleting it is ordinary file deletion, NOT forensic erasure: the data
     may remain on the disk until it is overwritten.
  4. the originals are fingerprinted again around the read / copy; if they
     changed (another program wrote meanwhile) the snapshot is retried and,
     if they keep changing, refused.

Development tool (stdlib only). Used by knowledge_audit.py and
brain_windows_check.py.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SIDECARS = ("-wal", "-shm", "-journal")
DATA_SIDECARS = ("-wal", "-journal")          # sidecars whose content matters
TEMP_PREFIX = "sk-audit-"
_CLOUD_NAMES = ("onedrive", "dropbox", "google drive", "googledrive", "my drive",
                "icloud drive", "iclouddrive", "icloud", "box", "box sync", "pcloud", "mega")
_CLOUD_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


class SnapshotRefused(Exception):
    """The snapshot was not made (a message for the user; nothing was changed)."""


Fingerprint = Dict[str, Optional[Tuple[int, int, str]]]   # file name -> (size, mtime_ns, sha256)


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(db_path: str) -> Fingerprint:
    """Size, modification time and SHA-256 of the database and its sidecars
    (None for a file that is not there). Only reads the files."""
    out: Fingerprint = {}
    for suffix in ("",) + SIDECARS:
        p = db_path + suffix
        if os.path.isfile(p):
            st = os.stat(p)
            out[os.path.basename(p)] = (st.st_size, st.st_mtime_ns, _sha_file(p))
        else:
            out[os.path.basename(p)] = None
    return out


def listing(folder: str) -> List[str]:
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def default_temp_base() -> str:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "Temp")
    return tempfile.gettempdir()


def cloud_reason(path: str) -> Optional[str]:
    """Why `path` looks cloud-synced (None if it does not)."""
    real = os.path.normcase(os.path.realpath(path))
    for var in _CLOUD_ENV:
        root = os.environ.get(var)
        if root:
            r = os.path.normcase(os.path.realpath(root))
            if real == r or real.startswith(r.rstrip("\\/") + os.sep):
                return f"it is inside your {var} folder"
    for part in real.replace("\\", "/").split("/"):
        p = part.strip().lower()
        if any(p == n or p.startswith(n + " -") or p.startswith(n + "-") for n in _CLOUD_NAMES) \
                or p.startswith("onedrive"):
            return f"it is inside a folder that looks cloud-synced ({part})"
    return None


def drive_reason(path: str) -> Optional[str]:
    """Why `path` is not a local fixed disk (Windows; None elsewhere / if fine)."""
    if sys.platform != "win32":
        return None
    real = os.path.realpath(path)
    if real.startswith("\\\\"):
        return "it is on a network location"
    import ctypes
    drive = os.path.splitdrive(real)[0] + "\\"
    kind = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive))
    if kind == 2:
        return "it is on removable media"
    if kind == 4:
        return "it is on a network drive"
    if kind not in (3,):            # 3 = fixed disk
        return "it is not on a local fixed disk"
    return None


def check_temp_base(base: str) -> None:
    if not os.path.isdir(base):
        raise SnapshotRefused(f"the temporary folder does not exist: {base}")
    why = cloud_reason(base) or drive_reason(base)
    if why:
        raise SnapshotRefused(f"refusing to put a temporary copy there: {why}. "
                              "Choose a local, non-synced folder with --temp-dir.")


def remove_leftovers(base: str) -> int:
    """Remove temporary copies left by an interrupted earlier run."""
    n = 0
    for name in listing(base):
        p = os.path.join(base, name)
        if name.startswith(TEMP_PREFIX) and os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            n += 0 if os.path.exists(p) else 1
    return n


@dataclass
class Snapshot:
    """An open, read-only view of a database: `con` is a connection to an
    in-memory image or to a private temporary copy - never the original."""
    con: sqlite3.Connection
    method: str                                  # "memory" | "disk copy"
    before: Fingerprint
    temp_dir: Optional[str] = None
    leftovers_removed: int = 0
    notes: List[str] = field(default_factory=list)
    temp_removed: Optional[bool] = None          # set by close() for a disk copy

    def close(self) -> None:
        try:
            self.con.close()
        finally:
            if self.temp_dir is not None:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                self.temp_removed = not os.path.exists(self.temp_dir)


def _wal_header_to_rollback(image: bytearray) -> None:
    """A database in WAL mode cannot be opened from memory; with an empty or
    absent -wal every committed change is in the main file, so marking the
    IN-MEMORY image as rollback mode reads exactly the same data."""
    if len(image) >= 20 and image[:16] == b"SQLite format 3\x00" and image[18] == 2:
        image[18] = image[19] = 1


def _has_data_sidecar(fp: Fingerprint, db_path: str) -> bool:
    base = os.path.basename(db_path)
    return any((fp.get(base + s) or (0,))[0] > 0 for s in DATA_SIDECARS)


def open_snapshot(db_path: str, temp_base: Optional[str] = None, retries: int = 3,
                  allow_memory: bool = True) -> Snapshot:
    """Open a read-only snapshot of `db_path` (see the module description).
    Raises SnapshotRefused if the database is missing or keeps changing, or
    the temporary folder is not acceptable."""
    if not os.path.isfile(db_path):
        raise SnapshotRefused("the database file does not exist")
    with open(db_path, "rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            raise sqlite3.DatabaseError("not an SQLite database")
    can_memory = allow_memory and hasattr(sqlite3.Connection, "deserialize")
    for attempt in range(retries):
        before = fingerprint(db_path)
        if can_memory and not _has_data_sidecar(before, db_path):
            with open(db_path, "rb") as fh:
                image = bytearray(fh.read())
            if hashlib.sha256(image).hexdigest() != before[os.path.basename(db_path)][2] \
                    or fingerprint(db_path) != before:
                continue                                    # changed while reading: retry
            _wal_header_to_rollback(image)
            con = sqlite3.connect(":memory:")
            try:
                con.deserialize(bytes(image))
                con.execute("PRAGMA query_only = 1")
            except sqlite3.DatabaseError:
                con.close()
                raise
            return Snapshot(con, "memory", before)
        base = temp_base or default_temp_base()
        check_temp_base(base)
        removed = remove_leftovers(base)
        tmp = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=base)      # private: 0700 / user profile
        try:
            name = os.path.basename(db_path)
            copy = os.path.join(tmp, name)
            for suffix in ("",) + DATA_SIDECARS:              # -shm is rebuilt from the -wal
                if before.get(name + suffix) is not None:
                    shutil.copyfile(db_path + suffix, copy + suffix)
            consistent = fingerprint(db_path) == before and all(
                before.get(name + s) is None or _sha_file(copy + s) == before[name + s][2]
                for s in ("",) + DATA_SIDECARS)
            if not consistent:
                shutil.rmtree(tmp, ignore_errors=True)
                continue
            con = sqlite3.connect(copy)          # recovery / log replay happen on the COPY
            try:
                con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                con.execute("PRAGMA query_only = 1")
            except sqlite3.DatabaseError:
                con.close()
                raise
            snap = Snapshot(con, "disk copy", before, tmp, removed)
            if not can_memory and not _has_data_sidecar(before, db_path):
                snap.notes.append("this Python cannot read a database from memory "
                                  "(needs 3.11+), so a temporary copy was used")
            return snap
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    raise SnapshotRefused("the database kept changing while it was being read - close "
                          "SK Assistant (and anything else using it) and try again")
