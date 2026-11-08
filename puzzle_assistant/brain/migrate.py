"""Safe migration of an older knowledge database into a NEW file (B5).

    from puzzle_assistant.brain.migrate import migrate_file
    report = migrate_file("knowledge.db", "knowledge.v4.db")

The original is never upgraded in place, and SQLite never opens it:
  1. its size, modification time and SHA-256 - and those of its -journal /
     -wal / -shm files - are recorded;
  2. the database and its -journal / -wal are COPIED byte for byte into
     the destination (which must not exist: nothing is ever overwritten),
     and once more into a private temporary folder as the reference;
  3. only the destination copy is opened and upgraded (a leftover journal
     is recovered there, never in the original);
  4. verification: integrity check; every original table (contributors,
     sources, sections, claims, evidence, links, versions, events) has the
     same rows in the same order as the reference; meta is the same except
     the format and index bookkeeping; a stored fact for every claim; the
     built-in relations present;
  5. the original's fingerprints must be exactly unchanged.
If anything fails, the destination is removed and an error says why; the
original is untouched either way. Using the new file, and what to do with
the original, are decisions taken afterwards - nothing here deletes or
renames it.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from typing import Dict, Optional, Tuple

from .relations import BUILTIN
from .schema import SCHEMA_VERSION
from .store import IMPORTER, SYSTEM, BrainError, KnowledgeStore, peek_schema_version

ORIGINAL_TABLES = ("contributors", "sources", "sections", "claims", "evidence", "claim_links",
                   "claim_versions", "claim_events")
_BOOKKEEPING = {"schema_version", "index_version", "fts_stale", "facts_stamp"}
_SIDECARS = ("-journal", "-wal", "-shm")


def _fingerprint(path: str) -> Dict[str, Optional[Tuple[int, int, str]]]:
    out = {}
    for suffix in ("",) + _SIDECARS:
        p = path + suffix
        if os.path.isfile(p):
            st = os.stat(p)
            with open(p, "rb") as fh:
                out[suffix] = (st.st_size, st.st_mtime_ns, hashlib.sha256(fh.read()).hexdigest())
        else:
            out[suffix] = None
    return out


def _copy(src: str, dst: str) -> None:
    for suffix in ("", "-journal", "-wal"):            # -shm is rebuilt from the -wal
        if os.path.isfile(src + suffix):
            shutil.copyfile(src + suffix, dst + suffix)


def _remove(path: str) -> None:
    for suffix in ("",) + _SIDECARS:
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def _rows(con, table: str):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    return cols, con.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()


def _verify(reference: str, upgraded: str) -> Dict[str, int]:
    ref = sqlite3.connect(reference)
    new = sqlite3.connect(f"file:{upgraded}?mode=ro", uri=True)
    try:
        if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BrainError("the upgraded copy failed its integrity check")
        counts = {}
        for t in ORIGINAL_TABLES:
            rcols, rrows = _rows(ref, t)
            ncols, nrows = _rows(new, t)
            # an upgrade may ADD columns (older formats); every original value must be kept,
            # in order. The only rows it may add are the Brain's two built-in contributors.
            idx = [ncols.index(c) for c in rcols]
            kept = [tuple(n[i] for i in idx) for n in nrows[:len(rrows)]]
            added = nrows[len(rrows):]
            if [tuple(r) for r in rrows] != kept or (added and (
                    t != "contributors" or {a[0] for a in added} - {SYSTEM, IMPORTER})):
                raise BrainError(f"the upgraded copy differs from the original in '{t}'")
            counts[t] = len(rrows)
        rmeta = {k: v for k, v in ref.execute("SELECT key, value FROM meta")
                 if k not in _BOOKKEEPING}
        nmeta = {k: v for k, v in new.execute("SELECT key, value FROM meta")
                 if k not in _BOOKKEEPING}
        if rmeta != nmeta:
            raise BrainError("the upgraded copy's settings differ from the original")
        if new.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != \
                str(SCHEMA_VERSION):
            raise BrainError("the copy was not upgraded")
        facts = new.execute("SELECT COUNT(*) FROM claim_facts").fetchone()[0]
        if facts != counts["claims"]:
            raise BrainError("the upgraded copy lacks stored facts")
        builtin = {r[0] for r in new.execute("SELECT key FROM relations WHERE builtin=1")}
        if builtin != set(BUILTIN):
            raise BrainError("the upgraded copy lacks the built-in relations")
        counts["claim_facts"] = facts
        return counts
    finally:
        ref.close()
        new.close()


def migrate_file(src: str, dst: str) -> dict:
    """Upgrade a copy of `src` into the new file `dst` (see the module
    description). Returns a report; raises BrainError (dst removed, src
    untouched) if it cannot be done safely."""
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if not os.path.isfile(src):
        raise BrainError("the database to migrate does not exist")
    if os.path.normcase(src) == os.path.normcase(dst):
        raise BrainError("the new file must be a different file")
    if any(os.path.exists(dst + s) for s in ("",) + _SIDECARS):
        raise BrainError("the new file already exists - nothing is ever overwritten")
    before = _fingerprint(src)
    try:
        version = peek_schema_version(src)
    except sqlite3.DatabaseError:
        raise BrainError("this is not an SK knowledge database (it cannot be read)")
    if version is None:
        raise BrainError("this is not an SK knowledge database")
    if version > SCHEMA_VERSION:
        raise BrainError(f"made by a newer SK Assistant (format {version})")
    tmp = tempfile.mkdtemp(prefix="sk_brain_migrate_")
    reference = os.path.join(tmp, "reference.db")
    ok = False
    try:
        _copy(src, dst)
        _copy(src, reference)
        if _fingerprint(src) != before:
            raise BrainError("the original changed while it was being copied - close every "
                             "program using it and try again")
        KnowledgeStore(dst, _upgrade=True).close()
        counts = _verify(reference, dst)
        if _fingerprint(src) != before:
            raise BrainError("the original changed during the migration")
        ok = True
        return {"from_version": version, "to_version": SCHEMA_VERSION, "source": src,
                "new_file": dst, "rows": counts, "original_unchanged": True}
    except sqlite3.DatabaseError as exc:
        raise BrainError(f"the database could not be migrated ({exc})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if not ok:
            _remove(dst)
