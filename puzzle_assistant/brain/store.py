"""KnowledgeStore: reading and writing knowledge.db with the Brain's rules.

Rules enforced here (not left to callers):
  * statuses, origins, evidence kinds and scopes must be known values;
  * 'verified' only through review(): needs a reviewer and at least one
    piece of direct or confirmation evidence (with a source); assertions -
    however many, from however many people - never count. This applies to
    AI suggestions too: an AI answer is never evidence by itself;
  * corrections create a new version and keep the old wording;
  * nothing is deleted; every change is an event with who / when / why;
  * a claim becomes shareable only by an explicit call, never when any of
    its evidence comes from a private source;
  * ISOLATION: every reading method takes a `viewer`. A personal or
    shared-candidate claim is visible only to its owner (and moderators
    for candidates); only 'shared' claims are visible to everyone. Writing
    to someone else's personal claim is refused;
  * no automatic verification: system / importer contributors cannot
    verify (except SK's own direct game observation), conflicts must be
    resolved before verifying, and derived evidence must name the claims
    it is derived from.
"""
from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from typing import Dict, List, Optional, Tuple

from .model import (AUTOMATIC_KINDS, EVIDENCE_KINDS, MODERATOR_KINDS, ORIGINS, SCOPES,
                    SOURCE_KINDS, STATUSES)
from .facts import compile_form, extract_all, key as name_key
from .relations import BUILTIN, CARDINALITIES, FACTS_METHOD, KEY_RE, VALUE_TYPES
from .schema import MIGRATIONS, SCHEMA, SCHEMA_VERSION, SHARED_SCHEMA
from .sqlite_ro import connect_readonly
from .text import INDEX_VERSION, index_text, normalize, polarity

SYSTEM = "system"
IMPORTER = "importer"


class BrainError(ValueError):
    """A request that would break the Brain's rules (nothing was changed)."""


class NeedsUpgrade(BrainError):
    """A library in an older format (B5.2): it was not opened and nothing in it
    was changed. Make a verified, upgraded COPY with brain.upgrade.upgrade_library()."""


def install_b6(con, role: str) -> None:
    """The B6 tables in ONE library database of `role` (personal | shared), in the
    caller's transaction: passages etc. everywhere, the original-path and
    suggestion tables only in personal files. The passage search index deletes
    for real (FTS5 secure-delete), so withdrawn / purged words don't linger in
    its segments. Sets meta layout=b6."""
    from .schema import LAYOUT, PASSAGE_SCHEMA, PERSONAL_SCHEMA
    for stmt in PASSAGE_SCHEMA.split(";") + (PERSONAL_SCHEMA.split(";") if role == "personal"
                                              else []):
        if stmt.strip():
            con.execute(stmt)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(terms)")
        con.execute("INSERT INTO passage_fts(passage_fts, rank) VALUES('secure-delete', 1)")
    except sqlite3.Error:
        pass                                      # no FTS5: plain search, same results
    con.execute("INSERT OR REPLACE INTO meta VALUES ('layout', ?)", (LAYOUT,))


def _refuse_old_library_file(path: str) -> None:
    """NeedsUpgrade for an existing library file of an older format - checked
    through a READ-ONLY connection, so the refused file is not touched."""
    from .schema import LAYOUT
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return
    try:
        # immutable: reads without creating -wal / -shm files next to an old file
        con = sqlite3.connect(file_uri(path, "ro") + "&immutable=1", uri=True)
    except sqlite3.Error:
        return
    try:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
                          ).fetchone()
        if has and con.execute("SELECT 1 FROM meta WHERE key='role'").fetchone() and \
                file_layout(con) != LAYOUT:
            raise NeedsUpgrade("this library is in an older format (B5.2); it was not opened "
                               "and nothing in it was changed - upgrade a copy with "
                               "brain.upgrade.upgrade_library()")
    except sqlite3.Error:
        pass
    finally:
        con.close()


def file_layout(con, schema: str = "main") -> Optional[str]:
    r = con.execute(f"SELECT value FROM {schema}.meta WHERE key='layout'").fetchone()
    return r[0] if r else None


class NeedsMigration(BrainError):
    """An older knowledge database: it is never upgraded in place. Make an
    upgraded, verified copy with brain.migrate.migrate_file(); the original
    is left exactly as it is."""


FACTS_VERSION = "1"


def _text_sha(text: str, subject: str) -> str:
    return hashlib.sha256(f"{text or ''}\0{subject or ''}".encode("utf-8")).hexdigest()[:16]


def peek_schema_version(path: str) -> Optional[int]:
    """The schema version of an existing database WITHOUT changing it (None
    for a missing / empty / brand-new file). Read-only connection; if that
    cannot read it (e.g. a leftover journal that would need recovery), the
    file and its sidecars are copied to a private temporary folder and the
    COPY is read - the original is never written."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None

    def read(con) -> Optional[int]:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
                          ).fetchone()
        if not has:
            return None
        r = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(r[0]) if r and str(r[0]).isdigit() else None
    try:
        con = connect_readonly(path)
        try:
            return read(con)
        finally:
            con.close()
    except sqlite3.OperationalError:
        pass
    tmp = tempfile.mkdtemp(prefix="sk_brain_peek_")
    try:
        copy = os.path.join(tmp, "k.db")
        for suffix in ("", "-journal", "-wal"):
            if os.path.isfile(path + suffix):
                shutil.copyfile(path + suffix, copy + suffix)
        con = sqlite3.connect(copy)
        try:
            return read(con)
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _atomic(fn):
    """A write is all or nothing. If anything fails part-way (a rule check,
    or SQLite reporting the file locked by another program), every change it
    made is rolled back, so a later write can never commit it by accident and
    the database is not left locked. Writes inside writes commit once, at the
    end of the outermost one."""
    @functools.wraps(fn)
    def run(self, *args, **kwargs):
        with self._lock:
            self._depth += 1
            try:
                result = fn(self, *args, **kwargs)
                if self._depth == 1 and self._db.in_transaction:
                    self._db.commit()
                return result
            except BaseException:
                if self._depth == 1 and self._db.in_transaction:
                    self._db.rollback()
                raise
            finally:
                self._depth -= 1
    return run


# B5.2: the tables a session reads as "mine + shared" (temporary views over the
# personal file and the read-only shared file). meta is never combined.
UNION_TABLES = ("contributors", "sources", "sections", "claims", "evidence", "claim_links",
                "claim_versions", "claim_events", "claim_index", "claim_facts", "relations",
                "entities", "aliases", "entity_links",
                # B6
                "source_versions", "passages", "passage_index", "passage_facts",
                "evidence_locations", "quote_checks")
# tables whose new rows take an id from the library's allocator (unique across files)
ALLOCATED = ("sources", "sections", "claims", "evidence", "claim_links", "claim_versions",
             "claim_events", "entities", "aliases", "entity_links",
             "source_versions", "passages", "claim_candidates")             # B6
ROLES = ("file", "personal", "shared")
PERSONAL_ONLY = ("source_files", "claim_candidates")          # B6: never in shared.db
_WITHDRAWN_SQL = ("(e.id IN (SELECT evidence_id FROM shared.evidence_withdrawn) OR e.source_id IN "
                  "(SELECT o.source_id FROM shared.source_opaque o JOIN shared.withdrawals w "
                  "ON w.opaque_id = o.opaque_id))")
# a shared claim is presented as verified only while some support is left: once
# withdrawn evidence leaves none, it reads as awaiting verification (its stored
# status is unchanged - only a moderator's review changes that)
_STATUS_SQL = (
    "CASE WHEN s.status='verified' AND EXISTS (SELECT 1 FROM shared.evidence e WHERE "
    f"e.claim_id=s.id AND {_WITHDRAWN_SQL}) AND NOT EXISTS (SELECT 1 FROM shared.evidence e "
    "LEFT JOIN shared.sources src ON src.id=e.source_id WHERE e.claim_id=s.id AND e.kind IN "
    "('direct','confirmation') AND e.source_id IS NOT NULL AND COALESCE(src.kind,'') != "
    f"'user_statement' AND NOT {_WITHDRAWN_SQL}) THEN 'awaiting_verification' ELSE s.status END "
    'AS "status"')


def _changed_sql(x: str, alias: str, extra_bad: str = "") -> str:
    """B6: a verified claim whose quotations all point at versions that are no
    longer current (and that has no other support left) reads as awaiting
    verification - its stored status is unchanged."""
    stale = (f"EXISTS (SELECT 1 FROM {x}.evidence e JOIN {x}.evidence_locations l ON "
             f"l.evidence_id=e.id JOIN {x}.source_versions v ON v.id=l.version_id WHERE "
             f"e.claim_id={alias}.id AND e.kind IN ('direct','confirmation') AND "
             f"v.state!='current')")
    good = (f"EXISTS (SELECT 1 FROM {x}.evidence e LEFT JOIN {x}.sources src ON "
            f"src.id=e.source_id WHERE e.claim_id={alias}.id AND e.kind IN "
            f"('direct','confirmation') AND e.source_id IS NOT NULL AND COALESCE(src.kind,'') "
            f"!= 'user_statement' AND NOT EXISTS (SELECT 1 FROM {x}.evidence_locations l JOIN "
            f"{x}.source_versions v ON v.id=l.version_id WHERE l.evidence_id=e.id AND "
            f"v.state!='current'){extra_bad})")
    return f"({alias}.status='verified' AND {stale} AND NOT {good})"


def file_uri(path: str, mode: str) -> str:
    import pathlib
    return pathlib.Path(os.path.abspath(path)).as_uri() + f"?mode={mode}"


def library_pragmas(con) -> None:
    """Every connection to a B5.2 library file: deleted content overwritten
    (secure_delete), no SQLite temporary files on disk, write-ahead log with
    full sync (a committed change survives power loss)."""
    con.execute("PRAGMA secure_delete = ON")
    con.execute("PRAGMA temp_store = MEMORY")
    con.execute("PRAGMA synchronous = FULL")


class KnowledgeStore:
    def __init__(self, path: str, _upgrade: bool = False, *, role: str = "file",
                 owner: Optional[Tuple[str, str, str]] = None, shared_path: Optional[str] = None,
                 allocator=None, namespace: str = ""):
        """`_upgrade` is only used by brain.migrate on its own new COPY: an
        existing older file is otherwise refused, unchanged (NeedsMigration).

        B5.2 (brain/library.py opens these; never needed by callers):
          role "file"      one development / test file (as before B5.2)
          role "personal"  ONE person's file (owner = (id, display name, kind));
                           with shared_path, the shared file is attached
                           READ-ONLY and reads see "mine + shared"; every write
                           goes to this file only
          role "shared"    the shared library file (moderator review methods)
        allocator(table) gives ids unique across the library's files."""
        if role not in ROLES:
            raise BrainError(f"unknown role {role!r}")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.role = role
        self.owner = owner[0] if owner else None
        self._owner_row = owner
        self.namespace = namespace
        self.shared_path = shared_path
        self.attached = False
        self._alloc = allocator
        self._lock = threading.RLock()
        self._depth = 0
        self._forms = None
        if role != "file":
            _refuse_old_library_file(path)          # immutable read: nothing is created
        v = peek_schema_version(path)
        if v is not None and v > SCHEMA_VERSION:
            raise BrainError(f"this knowledge database was made by a newer SK Assistant (format "
                             f"{v}, this version knows {SCHEMA_VERSION}); it was not opened, "
                             "so nothing in it was changed - please update SK Assistant")
        if v is not None and v < SCHEMA_VERSION and not _upgrade:
            raise NeedsMigration(
                f"this knowledge database is in an older format ({v}); it was not opened and "
                f"nothing in it was changed. Make an upgraded copy with "
                f"brain.migrate.migrate_file() - the original is kept as it is")
        if role == "file":
            self._db = sqlite3.connect(path, check_same_thread=False)
        else:
            _refuse_old_library_file(path)          # read-only look, before any read-write open
            self._db = sqlite3.connect(file_uri(path, "rwc"), uri=True, check_same_thread=False)
        try:
            self._open()
        except BaseException:
            self._db.close()                 # never leave the file open (Windows keeps it locked)
            raise

    def _open(self) -> None:
        self._db.row_factory = sqlite3.Row
        if self.role != "file":
            self._refuse_old_layout()
            library_pragmas(self._db)
            self._db.execute("PRAGMA journal_mode = WAL")
        # cross-file references (personal -> shared) cannot be foreign keys: in a
        # session the Brain checks them itself and records their namespace (xrefs)
        self._db.execute("PRAGMA foreign_keys = %s" % ("OFF" if self.shared_path else "ON"))
        self._db.executescript(SCHEMA)
        self._migrate()
        self.fts = self._setup_fts()
        for cid, name, kind in ((SYSTEM, "SK Assistant", "system"),
                                (IMPORTER, "Importer", "importer")):
            self._db.execute("INSERT OR IGNORE INTO main.contributors VALUES (?,?,?,?)",
                             (cid, name, kind, _now()))
        if self.role == "file":
            if self._meta("local_user") is None:
                uid = str(uuid.uuid4())
                self._db.execute("INSERT INTO main.contributors VALUES (?,?,?,?)",
                                 (uid, "Local user", "local_user", _now()))
                self._set_meta("local_user", uid)
        else:
            self._open_library_file()
        self._ensure_relations()
        self._db.commit()
        if self.shared_path:
            self._attach_shared()
        self._ensure_index()
        self._ensure_facts()

    # -- B5.2: library files ------------------------------------------------------
    def _refuse_old_layout(self) -> None:
        """An EXISTING library file without layout=b6 is refused before anything
        is written to it (B6: older libraries are upgraded only as a copy)."""
        from .schema import LAYOUT
        has = self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
                               ).fetchone()
        if not has:
            return                                # a brand-new file
        if self._db.execute("SELECT 1 FROM meta WHERE key='role'").fetchone() is None:
            return
        if file_layout(self._db) != LAYOUT:
            raise NeedsUpgrade("this library is in an older format (B5.2); it was not opened "
                               "and nothing in it was changed - upgrade a copy with "
                               "brain.upgrade.upgrade_library()")

    def _open_library_file(self) -> None:
        from .schema import LIBRARY_SCHEMA
        self._db.executescript(LIBRARY_SCHEMA)
        role = self._meta("role")
        if role is None:
            self._set_meta("role", self.role)
            self._set_meta("namespace", self.namespace or uuid.uuid4().hex)
            install_b6(self._db, self.role)
            if self.role == "personal":
                oid, name, kind = self._owner_row
                self._db.execute("INSERT OR IGNORE INTO main.contributors VALUES (?,?,?,?)",
                                 (oid, name, kind, _now()))
                self._set_meta("local_user", oid)
                self._set_meta("owner", oid)
        elif role != self.role:
            raise BrainError(f"this is a {role} file, not a {self.role} file")
        if self.role == "personal" and self._meta("owner") != self.owner:
            raise BrainError("this personal file belongs to someone else")
        self.namespace = self._meta("namespace")
        if self.role == "shared":
            self._db.executescript(SHARED_SCHEMA)

    def _attach_shared(self) -> None:
        """The shared file, READ-ONLY, behind "mine + shared" views."""
        from .schema import SCHEMA_VERSION as V
        self._db.execute("ATTACH DATABASE ? AS shared", (file_uri(self.shared_path, "ro"),))
        r = self._db.execute("SELECT value FROM shared.meta WHERE key IN ('role', 'schema_version') "
                             "ORDER BY key").fetchall()
        vals = [x[0] for x in r]
        if vals != ["shared", str(V)]:
            raise BrainError("the shared file is missing, of another kind or another format")
        from .schema import LAYOUT
        if file_layout(self._db, "shared") != LAYOUT:
            raise NeedsUpgrade("the shared file is in an older format (B5.2)")
        self.shared_namespace = self._db.execute(
            "SELECT value FROM shared.meta WHERE key='namespace'").fetchone()[0]
        try:
            self._db.execute("SELECT COUNT(*) FROM shared.claim_fts").fetchone()
        except sqlite3.Error:
            self.fts = False
        for t in UNION_TABLES:
            cols = [r[1] for r in self._db.execute(f'PRAGMA main.table_info("{t}")')]
            scols = {r[1] for r in self._db.execute(f'PRAGMA shared.table_info("{t}")')}
            if set(cols) != scols:
                raise BrainError(f"the shared file's '{t}' table has other columns")
            c = ", ".join(f'"{x}"' for x in cols)
            shared_part = f"SELECT {c} FROM shared.{t}"
            main_part = f"SELECT {c} FROM main.{t}"
            if t == "claims":                   # verified only while support remains (B5.2)
                ch = _changed_sql("shared", "s", f" AND NOT {_WITHDRAWN_SQL}")
                c2 = ", ".join(f'"{x}"' if x != "status" else
                               f"CASE WHEN {ch} THEN 'awaiting_verification' ELSE "
                               + _STATUS_SQL.replace('END AS "status"', 'END END AS "status"')
                               for x in cols)
                shared_part = f"SELECT {c2} FROM shared.claims s"
                c3 = ", ".join(f'"{x}"' if x != "status" else
                               f"CASE WHEN {_changed_sql('main', 'm')} THEN "
                               "'awaiting_verification' ELSE m.status END AS \"status\""
                               for x in cols)
                main_part = f"SELECT {c3} FROM main.claims m"
            key = {"contributors": "id", "relations": "key", "sources": "id",
                   "evidence": "id"}.get(t)
            if key:                             # one's own full row wins over the shared one
                shared_part += (" WHERE " if t != "claims" else " AND ") + \
                    f'"{key}" NOT IN (SELECT "{key}" FROM main.{t})'
            self._db.execute(f'CREATE TEMP VIEW "{t}" AS {main_part} UNION ALL {shared_part}')
        self.attached = True
        clash = self.id_collisions()
        if clash:
            self._db.close()
            raise BrainError("record numbers in your file and the shared file collide "
                             f"({', '.join(f'{t} {i}' for t, i in clash[:5])}); the files were "
                             "not changed - run the library repair before using them")

    def id_collisions(self) -> List[Tuple[str, int]]:
        """(table, id) present in BOTH the personal and the shared file (should
        never happen; checked at every session start)."""
        out = []
        for t in ALLOCATED:
            if t in PERSONAL_ONLY:
                continue                          # never in shared.db
            for r in self._db.execute(f"SELECT id FROM main.{t} INTERSECT SELECT id FROM "
                                      f"shared.{t} WHERE id NOT IN (SELECT id FROM "
                                      f"shared.projections WHERE tbl=?)", (t,)):
                out.append((t, r[0]))
        # a cross-file reference must still point at the same file (namespace)
        for t, rid, col, ns, tid in self._db.execute("SELECT * FROM main.xrefs").fetchall():
            if ns not in (self.shared_namespace, "inaccessible"):
                out.append((f"{t}.{col} -> another file", rid))
        return out

    def _new(self, table: str, sql: str, params) -> sqlite3.Cursor:
        """INSERT a new row; in a library its id comes from the allocator."""
        if self._alloc is None:
            return self._db.execute(sql, params)
        head = f"INTO main.{table} ("
        assert head in sql, sql
        sql = sql.replace(head, head + "id, ", 1).replace("VALUES (", "VALUES (?,", 1)
        return self._db.execute(sql, (self._alloc(table),) + tuple(params))

    def _xref(self, table: str, row_id: int, column: str, target_table: str,
              target_id: Optional[int]) -> None:
        """Record that a row of this personal file points at a SHARED row
        (namespace + id), so the reference keeps its meaning."""
        if not self.attached or target_id is None:
            return
        if self._db.execute(f"SELECT 1 FROM main.{target_table} WHERE id=?",
                            (target_id,)).fetchone():
            return
        if self._db.execute(f"SELECT 1 FROM shared.{target_table} WHERE id=?",
                            (target_id,)).fetchone():
            self._db.execute("INSERT OR REPLACE INTO main.xrefs VALUES (?,?,?,?,?)",
                             (table, row_id, column, self.shared_namespace, target_id))

    def _shared_schema(self) -> Optional[str]:
        """Where the shared-only tables are: 'shared' in a session, 'main' in
        the shared file itself, None for a file without them."""
        if self.attached:
            return "shared"
        return "main" if self.role == "shared" else None

    def withdrawn_evidence(self, claim_id: int) -> set:
        """Evidence of a claim that its source's owner withdrew (B5.2): never
        support, never cited."""
        sh = self._shared_schema()
        if sh is None:
            return set()
        return {r[0] for r in self._db.execute(
            f"SELECT e.id FROM evidence e WHERE e.claim_id=? AND (e.id IN (SELECT evidence_id FROM "
            f"{sh}.evidence_withdrawn) OR e.source_id IN (SELECT o.source_id FROM {sh}.source_opaque "
            f"o JOIN {sh}.withdrawals w ON w.opaque_id = o.opaque_id))", (claim_id,))}

    def _check_viewer(self, viewer: Optional[str]) -> None:
        if self.role == "personal" and viewer not in (None, "", self.owner):
            raise BrainError("this session belongs to another person")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- search index (B2): derived from claims, never the other way round ---------
    def _setup_fts(self) -> bool:
        try:
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS claim_fts USING fts5(terms)")
            self._db.execute("SELECT COUNT(*) FROM claim_fts").fetchone()   # usable, not just present
            self._db.commit()
            return True
        except sqlite3.Error:
            return False                     # SQLite without FTS5 (or a damaged table): Python search

    def _fts_off(self) -> None:
        """FTS5 failed while running: carry on with the plain Python search
        (same results) and have the next start rebuild the FTS5 table."""
        self.fts = False
        self._set_meta("fts_stale", "1")

    def _index_claim(self, claim_id: int, text: str, subject: str, category: str) -> None:
        self._store_fact(claim_id, text, subject)
        t = index_text(text, subject or "", category or "")
        self._db.execute("INSERT OR REPLACE INTO main.claim_index (claim_id, terms, subject_terms, norm, "
                         "subject_norm, positive) VALUES (?,?,?,?,?,?)",
                         (claim_id, t, index_text(subject or ""),
                          normalize(f"{subject or ''} {text}"), normalize(subject or ""),
                          int(polarity(text))))
        if self.fts:
            try:
                self._db.execute("DELETE FROM main.claim_fts WHERE rowid=?", (claim_id,))
                self._db.execute("INSERT INTO main.claim_fts (rowid, terms) VALUES (?,?)", (claim_id, t))
            except sqlite3.Error:
                self._fts_off()
        else:                                # written without FTS5: that table is now behind
            self._set_meta("fts_stale", "1")

    def _ensure_index(self) -> None:
        """Rebuild the index if it is missing, incomplete or from an older
        version of the word rules. Only index tables are written."""
        with self._lock:
            cols = {r[1] for r in self._db.execute("PRAGMA main.table_info(claim_index)")}
            if "positive" not in cols:       # index from an earlier development build:
                self._db.execute("DROP TABLE main.claim_index")      # derived data, rebuilt below
                self._db.executescript(SCHEMA)
            n_claims = self._db.execute("SELECT COUNT(*) FROM main.claims").fetchone()[0]
            n_index = self._db.execute("SELECT COUNT(*) FROM main.claim_index").fetchone()[0]
            n_fts = n_claims
            if self.fts:
                try:
                    n_fts = self._db.execute("SELECT COUNT(*) FROM main.claim_fts").fetchone()[0]
                except sqlite3.Error:
                    self.fts = False
            fts_stale = self.fts and self._meta("fts_stale") == "1"
            if (self._meta("index_version") == str(INDEX_VERSION) and n_index == n_claims
                    and n_fts == n_claims and not fts_stale):
                return
            self._db.execute("DELETE FROM main.claim_index")
            if self.fts:
                try:
                    self._db.execute("DELETE FROM main.claim_fts")
                except sqlite3.Error:
                    self.fts = False
            for r in self._db.execute("SELECT id, text, subject, category FROM main.claims"
                                      ).fetchall():
                self._index_claim(r[0], r[1], r[2], r[3])
            self._set_meta("index_version", str(INDEX_VERSION))
            if self.fts:
                self._db.execute("DELETE FROM main.meta WHERE key='fts_stale'")
            self._db.commit()

    # -- structured facts (B5): derived from claims, like the search index -------
    def _store_fact(self, claim_id: int, text: str, subject: str) -> None:
        r = self._db.execute("SELECT version FROM main.claims WHERE id=?", (claim_id,)).fetchone()
        f = extract_all(text or "", subject or "", self.custom_forms())
        method = FACTS_METHOD if f is None or f.relation in BUILTIN else f"relation:{f.relation}"
        self._db.execute(
            "INSERT OR REPLACE INTO main.claim_facts (claim_id, claim_version, text_sha, relation, "
            "place, value, absent, qualifier, display, method) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (claim_id, r[0] if r else 1, _text_sha(text, subject),
             None if f is None else f.relation, None if f is None else f.place,
             None if f is None else f.value, int(bool(f and f.absent)),
             "" if f is None else f.qualifier, "" if f is None else f.display, method))

    def _facts_stamp(self) -> str:
        rows = self._db.execute("SELECT key, value_type, sentence_forms FROM relations "
                                "WHERE builtin=0 ORDER BY key").fetchall()
        return FACTS_VERSION + ":" + hashlib.sha256(
            json.dumps([list(r) for r in rows]).encode()).hexdigest()[:12]

    def _ensure_facts(self) -> None:
        """Rebuild the stored facts if missing, incomplete, or read with other
        rules (version / added relations). Only claim_facts is written."""
        with self._lock:
            if self._meta("facts_stamp") != self._facts_stamp():
                self._rebuild_facts()
                self._db.commit()
                return
            stale = [r for r in self._db.execute(
                "SELECT c.id, c.text, c.subject, c.version, f.text_sha, f.claim_version "
                "FROM main.claims c LEFT JOIN main.claim_facts f ON f.claim_id = c.id").fetchall()
                if r[4] != _text_sha(r[1], r[2]) or r[5] != r[3]]
            if not stale:
                return
            for r in stale:              # missing, or the claim changed outside the Brain
                self._store_fact(r[0], r[1], r[2])
            from .retrieve import forget_cache
            forget_cache(self)
            self._db.commit()

    def _rebuild_facts(self) -> None:
        self._forms = None
        self._db.execute("DELETE FROM main.claim_facts")
        for r in self._db.execute("SELECT id, text, subject FROM main.claims").fetchall():
            self._store_fact(r[0], r[1], r[2])
        self._set_meta("facts_stamp", self._facts_stamp())
        from .retrieve import forget_cache            # cached facts are now out of date
        forget_cache(self)

    def stored_fact(self, claim_id: int) -> Optional[dict]:
        """The stored fact row of a claim (no viewer: callers check visibility)."""
        with self._lock:
            r = self._db.execute("SELECT * FROM claim_facts WHERE claim_id=?",
                                 (claim_id,)).fetchone()
        return dict(r) if r else None

    # -- relations as data (B5) --------------------------------------------------
    def _ensure_relations(self) -> None:
        for k, (vtype, card, changes, labels) in BUILTIN.items():
            self._db.execute(
                "INSERT OR IGNORE INTO main.relations (key, value_type, cardinality, changes, labels, "
                "builtin, added_by, added_at) VALUES (?,?,?,?,?,1,?,?)",
                (k, vtype, card, int(changes), json.dumps(labels, ensure_ascii=False), SYSTEM,
                 _now()))

    def relations(self) -> List[dict]:
        with self._lock:
            out = []
            for r in self._db.execute("SELECT * FROM relations ORDER BY builtin DESC, key"):
                d = dict(r)
                for k in ("labels", "sentence_forms", "question_forms"):
                    d[k] = json.loads(d[k] or "null")
                out.append(d)
            return out

    def custom_forms(self) -> list:
        """Compiled sentence forms of the ADDED relations (none by default)."""
        if self._forms is None:
            forms = []
            for r in self._db.execute("SELECT key, value_type, sentence_forms FROM relations "
                                      "WHERE builtin=0 ORDER BY key").fetchall():
                for t in json.loads(r[2] or "[]"):
                    forms.append(compile_form(r[0], r[1], t))
            self._forms = forms
        return self._forms

    def question_forms(self) -> List[tuple]:
        """(relation, template) of the ADDED relations' question forms."""
        with self._lock:
            out = []
            for r in self._db.execute("SELECT key, question_forms FROM relations WHERE builtin=0 "
                                      "ORDER BY key").fetchall():
                out += [(r[0], t) for t in json.loads(r[1] or "[]")]
            return out

    def relation_labels(self) -> Dict[str, dict]:
        with self._lock:
            return {r[0]: json.loads(r[1] or "{}") for r in
                    self._db.execute("SELECT key, labels FROM relations")}

    @_atomic
    def add_relation(self, key: str, value_type: str, sentence_forms: List[str],
                     question_forms: List[str], labels: Optional[dict] = None,
                     cardinality: str = "one", changes: bool = False,
                     by: Optional[str] = None) -> None:
        """Add a relation as data. Only the installation's own user or a
        moderator may (it changes how every claim is read); the stored facts
        are re-read at once. B3's built-in relations cannot be changed."""
        by = by or self.local_user
        with self._lock:
            kind = self._known_contributor(by)
            if kind not in ("local_user",) + MODERATOR_KINDS:
                raise BrainError("only the owner of this installation or a moderator can add a "
                                 "relation")
            if not KEY_RE.match(key or "") or key in BUILTIN:
                raise BrainError(f"bad or built-in relation key {key!r}")
            if self._db.execute("SELECT 1 FROM relations WHERE key=?", (key,)).fetchone():
                raise BrainError(f"relation {key!r} exists already")
            if value_type not in VALUE_TYPES or cardinality not in CARDINALITIES:
                raise BrainError("unknown value type or cardinality")
            if not sentence_forms:
                raise BrainError("a relation needs at least one sentence form")
            try:
                for t in sentence_forms:
                    compile_form(key, value_type, t)
            except ValueError as exc:
                raise BrainError(str(exc))
            for q in question_forms:
                if (q or "").count("{place}") != 1 or "{value}" in q:
                    raise BrainError("a question form needs exactly one {place} and no {value}")
            self._db.execute(
                "INSERT INTO main.relations (key, value_type, cardinality, changes, labels, "
                "sentence_forms, question_forms, builtin, added_by, added_at) "
                "VALUES (?,?,?,?,?,?,?,0,?,?)",
                (key, value_type, cardinality, int(changes),
                 json.dumps(labels or {"en": key.replace("_", " ")}, ensure_ascii=False),
                 json.dumps(list(sentence_forms), ensure_ascii=False),
                 json.dumps(list(question_forms), ensure_ascii=False), by, _now()))
            self._rebuild_facts()

    # -- entities, aliases, "same as" (B5): created by a person, never merged
    #    automatically ------------------------------------------------------------
    def _person_or_proposal(self, by: str) -> str:
        """'approved' for a person, 'proposed' for an automatic contributor."""
        return "proposed" if self._known_contributor(by) in AUTOMATIC_KINDS else "approved"

    def _entity(self, entity_id: int) -> Optional[dict]:
        r = self._db.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        return dict(r) if r else None

    def _sees(self, row: Optional[dict], viewer: str) -> bool:
        return row is not None and (row["scope"] == "shared" or row["owner_id"] == viewer)

    @_atomic
    def add_entity(self, name: str, kind: str = "", scope: str = "personal",
                   by: Optional[str] = None) -> int:
        """A person, place or thing. Never merged with another one, even with
        the same name (two people can share a name). Automatic contributors
        only propose; shared entities are created by moderators."""
        by = by or self.local_user
        if not name_key(name or ""):
            raise BrainError("an entity needs a name")
        if scope not in ("personal", "shared"):
            raise BrainError("an entity is personal or shared")
        if self.role == "personal" and scope != "personal":
            raise BrainError("a personal file holds personal entities only")
        if self.role == "shared" and scope != "shared":
            raise BrainError("the shared file holds shared entities only")
        with self._lock:
            status = self._person_or_proposal(by)
            if scope == "shared" and self._known_contributor(by) not in MODERATOR_KINDS:
                raise BrainError("shared entities are created by moderators")
            cur = self._new("entities", "INSERT INTO main.entities (name, key, kind, status, scope, owner_id, created) "
                "VALUES (?,?,?,?,?,?,?)", (name.strip(), name_key(name), (kind or "").strip(),
                                           status, scope, by, _now()))
            return cur.lastrowid

    @_atomic
    def add_alias(self, entity_id: int, alias: str, language: str = "",
                  by: Optional[str] = None) -> int:
        """Another name for an entity. Approved at once when a person who may
        change the entity adds it; an automatic contributor (importer, SK
        itself) only PROPOSES it - to the entity's owner, who must approve it."""
        by = by or self.local_user
        with self._lock:
            e = self._entity(entity_id)
            automatic = self._known_contributor(by) in AUTOMATIC_KINDS
            if e is None or not (automatic or self._sees(e, by)):
                raise BrainError(f"no entity {entity_id}")
            if self.attached and not self._db.execute("SELECT 1 FROM main.entities WHERE id=?",
                                                      (entity_id,)).fetchone():
                raise BrainError("names for shared entities are added by moderators")
            if e["scope"] == "shared" and not automatic and \
                    self._known_contributor(by) not in MODERATOR_KINDS:
                raise BrainError("aliases of shared entities are added by moderators")
            k = name_key(alias or "")
            if not k or k == e["key"]:
                raise BrainError("an alias needs a different name")
            status = self._person_or_proposal(by)
            cur = self._new("aliases", "INSERT INTO main.aliases (entity_id, alias, key, language, status, scope, owner_id, "
                "added_by, added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (entity_id, alias.strip(), k, language, status, e["scope"], e["owner_id"], by,
                 _now()))
            return cur.lastrowid

    @_atomic
    def approve_alias(self, alias_id: int, by: Optional[str] = None) -> None:
        by = by or self.local_user
        with self._lock:
            if self._known_contributor(by) in AUTOMATIC_KINDS:
                raise BrainError("aliases are approved by a person")
            a = self._db.execute("SELECT * FROM aliases WHERE id=?", (alias_id,)).fetchone()
            if a is None or not self._sees(dict(a), by):
                raise BrainError(f"no alias {alias_id}")
            if a["scope"] == "shared" and self._known_contributor(by) not in MODERATOR_KINDS:
                raise BrainError("aliases of shared entities are approved by moderators")
            self._db.execute("UPDATE main.aliases SET status='approved' WHERE id=?", (alias_id,))

    @_atomic
    def link_entities(self, a: int, b: int, by: Optional[str] = None) -> int:
        """"Same as": only a person adds it, never the Brain on its own. Kept
        as history; unlink_entities() makes it inactive again."""
        by = by or self.local_user
        with self._lock:
            if self._known_contributor(by) in AUTOMATIC_KINDS:
                raise BrainError("only a person can say two entities are the same")
            ea, eb = self._entity(a), self._entity(b)
            if a == b or not self._sees(ea, by) or not self._sees(eb, by):
                raise BrainError("no such pair of entities")
            shared = ea["scope"] == eb["scope"] == "shared"
            if shared and self._known_contributor(by) not in MODERATOR_KINDS:
                raise BrainError("shared entities are linked by moderators")
            cur = self._new("entity_links", "INSERT INTO main.entity_links (a, b, scope, owner_id, active, added_by, added_at) "
                "VALUES (?,?,?,?,1,?,?)", (a, b, "shared" if shared else "personal", by, by, _now()))
            if shared and self.role == "personal":
                raise BrainError("shared entities are linked by moderators")
            self._xref("entity_links", cur.lastrowid, "a", "entities", a)
            self._xref("entity_links", cur.lastrowid, "b", "entities", b)
            return cur.lastrowid

    @_atomic
    def unlink_entities(self, link_id: int, by: Optional[str] = None) -> None:
        by = by or self.local_user
        with self._lock:
            row = self._db.execute("SELECT * FROM entity_links WHERE id=?", (link_id,)).fetchone()
            if row is None or not self._sees(dict(row), by) or not row["active"]:
                raise BrainError(f"no active link {link_id}")
            if row["scope"] == "shared" and self._known_contributor(by) not in MODERATOR_KINDS:
                raise BrainError("shared links are removed by moderators")
            self._db.execute("UPDATE main.entity_links SET active=0, removed_by=?, removed_at=? "
                             "WHERE id=?", (by, _now(), link_id))

    def entities(self, viewer: Optional[str] = None) -> List[dict]:
        viewer = viewer or self.local_user
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM entities WHERE scope='shared' OR owner_id=? ORDER BY id", (viewer,))]

    def alias_map(self, viewer: Optional[str] = None) -> Dict[str, List[str]]:
        """name key -> the canonical name key(s) it stands for, for `viewer`:
        approved entities and aliases the viewer may see (shared, or their
        own), joined by active "same as" links. A name standing for more than
        one canonical name is ambiguous - reasoning never picks one."""
        viewer = viewer or self.local_user
        with self._lock:
            ents = {r["id"]: dict(r) for r in self._db.execute(
                "SELECT * FROM entities WHERE status='approved' AND (scope='shared' OR owner_id=?)",
                (viewer,))}
            aliases = [dict(r) for r in self._db.execute(
                "SELECT * FROM aliases WHERE status='approved' AND (scope='shared' OR owner_id=?)",
                (viewer,))]
            links = [dict(r) for r in self._db.execute(
                "SELECT * FROM entity_links WHERE active=1 AND (scope='shared' OR owner_id=?)",
                (viewer,))]
        parent = {i: i for i in ents}

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for l in links:
            if l["a"] in ents and l["b"] in ents:
                ra, rb = root(l["a"]), root(l["b"])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        out: Dict[str, set] = {}

        def add(k, canon):
            out.setdefault(k, set()).add(canon)
        for i, e in ents.items():
            add(e["key"], ents[root(i)]["key"])
        for a in aliases:
            if a["entity_id"] in ents:
                add(a["key"], ents[root(a["entity_id"])]["key"])
        return {k: sorted(v) for k, v in out.items() if v != {k}}

    def alias_names(self, viewer: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """(key, name as written, entity kind) of the approved entities and
        aliases `viewer` may see (shared, or their own) - the same rule as
        alias_map. Read-only; used to understand questions (B5.1)."""
        viewer = viewer or self.local_user
        with self._lock:
            ents = [(r[0], r[1], r[2]) for r in self._db.execute(
                "SELECT key, name, kind FROM entities WHERE status='approved' AND "
                "(scope='shared' OR owner_id=?) ORDER BY id", (viewer,))]
            als = [(r[0], r[1], r[2]) for r in self._db.execute(
                "SELECT a.key, a.alias, e.kind FROM aliases a JOIN entities e ON e.id=a.entity_id "
                "WHERE a.status='approved' AND (a.scope='shared' OR a.owner_id=?) AND "
                "e.status='approved' AND (e.scope='shared' OR e.owner_id=?) ORDER BY a.id",
                (viewer, viewer))]
        return ents + als

    def _visible_sql(self, viewer: Optional[str], alias: str = "c", review: bool = True):
        """WHERE-clause part that keeps only claims `viewer` may see. With
        review=False (search), other people's proposals are left out even for
        moderators: unreviewed proposals are for reviewing, not for answers."""
        self._check_viewer(viewer)
        viewer = viewer or self.local_user
        r = self._db.execute("SELECT kind FROM contributors WHERE id=?", (viewer,)).fetchone()
        mod = review and bool(r) and r[0] in MODERATOR_KINDS
        clause = (f"({alias}.scope='shared' OR {alias}.owner_id=?"
                  + (f" OR {alias}.scope='shared_candidate'" if mod else "") + ")")
        return clause, [viewer]

    def visible_keys(self, viewer: Optional[str], match_terms: Optional[List[str]] = None,
                     use_fts: Optional[bool] = None, ids_only: bool = False):
        """(id, status, scope, owner_id, version) - or just (id,) with ids_only -
        of the claims viewer's search may use, and whether they were already
        narrowed to `match_terms` by FTS5. Without FTS5 all visible claims are
        returned and the caller narrows them (identical eligibility)."""
        use_fts = self.fts if use_fts is None else (use_fts and self.fts)
        with self._lock:
            vis, args = self._visible_sql(viewer, review=False)
            cols = "c.id" if ids_only else "c.id, c.status, c.scope, c.owner_id, c.version"
            q = f"SELECT {cols} FROM claims c WHERE {vis}"
            # a session reads two files: keep the single file's order (record number)
            order = " ORDER BY c.id" if self.attached else ""
            if match_terms and use_fts:
                try:
                    m = " OR ".join('"%s"' % t.replace('"', '') for t in match_terms)
                    return [tuple(r) for r in self._db.execute(
                        q + " AND c.id IN " + self._fts_in() + order,
                        args + self._fts_args(m))], True
                except sqlite3.Error:        # FTS5 broke while running: same search in Python
                    self._fts_off()
                    self._db.commit()
            return [tuple(r) for r in self._db.execute(q + order, args)], not match_terms

    def index_rows(self, viewer: Optional[str], ids: List[int]) -> List[dict]:
        """Search rows of these claims - only those the viewer's
        search may use, whatever ids are asked for."""
        out: List[dict] = []
        with self._lock:
            vis, args = self._visible_sql(viewer, review=False)
            for n in range(0, len(ids), 500):
                chunk = ids[n:n + 500]
                out += [dict(r) for r in self._db.execute(
                    "SELECT c.id, c.text, c.subject, c.category, c.status, c.origin, c.scope, "
                    "c.owner_id, c.version, i.terms, i.subject_terms, i.norm, i.subject_norm, "
                    "i.positive, f.claim_version AS f_version, f.text_sha AS f_sha, "
                    "f.relation AS f_relation, f.place AS f_place, f.value AS f_value, "
                    "f.absent AS f_absent, f.qualifier AS f_qualifier, f.display AS f_display "
                    "FROM claims c JOIN claim_index i ON i.claim_id = c.id "
                    "LEFT JOIN claim_facts f ON f.claim_id = c.id "
                    f"WHERE {vis} AND c.id IN ({','.join('?' * len(chunk))})"
                    + (" ORDER BY c.id" if self.attached else ""), args + chunk)]
        return out

    def visible_subjects(self, viewer: Optional[str], after_id: int = 0) -> List[tuple]:
        """(subject, normalised subject) pairs of the viewer's visible claims,
        each once, sorted (with after_id: only from claims newer than it)."""
        with self._lock:
            vis, args = self._visible_sql(viewer, review=False)
            return [tuple(r) for r in self._db.execute(
                f"SELECT DISTINCT c.subject, i.subject_norm FROM claims c "
                f"JOIN claim_index i ON i.claim_id = c.id WHERE {vis} AND c.subject != '' "
                f"AND c.id > ? ORDER BY c.subject, i.subject_norm", args + [after_id])]

    def changed_since(self, mark: int):
        """(newest event id, ids of claims with an event after `mark`). Every
        change to a claim - created, reviewed, corrected, flagged, shared -
        records an event on that claim in the same transaction, so this is
        exactly the set of claims changed since `mark` (also by another
        program using the Brain)."""
        with self._lock:
            newest = self._db.execute("SELECT COALESCE(MAX(id), 0) FROM claim_events").fetchone()[0]
            if newest <= mark:
                return newest, set()
            return newest, {r[0] for r in self._db.execute(
                "SELECT DISTINCT claim_id FROM claim_events WHERE id > ?", (mark,))}

    def subjects_stamp(self) -> tuple:
        """Changes when the subjects someone may see can change: a new claim
        (newest id), a correction, or a sharing step. Reviews don't move it."""
        with self._lock:
            return tuple(self._db.execute(
                "SELECT (SELECT COALESCE(MAX(id), 0) FROM claims), (SELECT COUNT(*) FROM "
                "claim_events WHERE event IN ('corrected', 'share_proposed', 'share_approved')), "
                "(SELECT COUNT(*) FROM contributors)").fetchone())

    def _fts_in(self) -> str:
        if self.attached:
            return ("(SELECT rowid FROM main.claim_fts WHERE claim_fts MATCH ? UNION ALL "
                    "SELECT rowid FROM shared.claim_fts WHERE claim_fts MATCH ?)")
        return "(SELECT rowid FROM main.claim_fts WHERE claim_fts MATCH ?)"

    def _fts_args(self, m: str) -> list:
        return [m, m] if self.attached else [m]

    def data_version(self):
        """SQLite's data_version: changes when ANY OTHER connection (another
        program, another Brain, a raw edit, a restore) commits to the file
        (in a session: to either file)."""
        with self._lock:
            v = self._db.execute("PRAGMA main.data_version").fetchone()[0]
            if self.attached:
                return (v, self._db.execute("PRAGMA shared.data_version").fetchone()[0])
            return v

    def visible_count(self, viewer: Optional[str]) -> int:
        with self._lock:
            vis, args = self._visible_sql(viewer, review=False)
            return self._db.execute(f"SELECT COUNT(*) FROM claims c WHERE {vis}", args).fetchone()[0]

    def visible_term_counts(self, viewer: Optional[str], words: List[str],
                            use_fts: Optional[bool] = None) -> Dict[str, int]:
        """In how many of the viewer's visible claims each word occurs
        (word rarity is never computed from claims they can't see)."""
        use_fts = self.fts if use_fts is None else (use_fts and self.fts)
        out: Dict[str, int] = {}
        with self._lock:
            vis, args = self._visible_sql(viewer, review=False)
            if use_fts:
                try:
                    for w in words:
                        out[w] = self._db.execute(
                            f"SELECT COUNT(*) FROM claims c WHERE {vis} AND c.id IN "
                            + self._fts_in(), args + self._fts_args('"%s"' % w.replace('"', ''))
                        ).fetchone()[0]
                    return out
                except sqlite3.Error:        # FTS5 broke while running: count in Python
                    self._fts_off()
                    self._db.commit()
                    out = {}
            rows = self._db.execute(f"SELECT i.terms FROM claims c JOIN claim_index i "
                                    f"ON i.claim_id = c.id WHERE {vis}", args).fetchall()
        sets = [set(r[0].split()) for r in rows]
        for w in words:
            out[w] = sum(1 for t in sets if w in t)
        return out

    def _migrate(self) -> None:
        """Bring an older knowledge.db up to SCHEMA_VERSION; nothing is deleted."""
        cur = self._meta("schema_version")
        if cur is None:                      # brand-new file: created at the latest version
            self._set_meta("schema_version", str(SCHEMA_VERSION))
            return
        if int(cur) > SCHEMA_VERSION:        # e.g. after rolling back to an older SK version
            raise BrainError(f"this knowledge database was made by a newer SK Assistant (format "
                             f"{cur}, this version knows {SCHEMA_VERSION}); it was not opened, "
                             "so nothing in it was changed - please update SK Assistant")
        for v in range(int(cur) + 1, SCHEMA_VERSION + 1):
            for sql in MIGRATIONS.get(v, []):
                try:
                    self._db.execute(sql)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc):
                        raise
            self._set_meta("schema_version", str(v))
        self._db.commit()

    # -- meta ------------------------------------------------------------------
    def _meta(self, key: str) -> Optional[str]:
        r = self._db.execute("SELECT value FROM main.meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO main.meta VALUES (?,?)", (key, value))

    @property
    def local_user(self) -> str:
        return self._meta("local_user")

    @property
    def schema_version(self) -> int:
        return int(self._meta("schema_version") or 0)

    @_atomic
    def add_contributor(self, display_name: str, kind: str = "external_user",
                        contributor_id: Optional[str] = None) -> str:
        cid = contributor_id or str(uuid.uuid4())
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO main.contributors VALUES (?,?,?,?)",
                             (cid, display_name, kind, _now()))
        return cid

    def _known_contributor(self, cid: str) -> str:
        """Returns the contributor's kind."""
        r = self._db.execute("SELECT kind FROM contributors WHERE id=?", (cid,)).fetchone()
        if r is None:
            raise BrainError(f"unknown contributor {cid!r}")
        return r[0]

    # -- isolation -------------------------------------------------------------------
    def searchable(self, c: Optional[dict], viewer: Optional[str]) -> bool:
        """May `c` appear in viewer's search results: shared, or their own."""
        self._check_viewer(viewer)
        return c is not None and (c["scope"] == "shared" or c["owner_id"] == (viewer or self.local_user))

    def can_see(self, c: dict, viewer: Optional[str]) -> bool:
        """Personal / candidate claims belong to their owner; shared ones to all.
        Moderators also see candidates (to review them)."""
        if c is None:
            return False
        self._check_viewer(viewer)
        if c["scope"] == "shared":
            return True
        viewer = viewer or self.local_user
        if c["owner_id"] == viewer:
            return True
        if c["scope"] == "shared_candidate":
            r = self._db.execute("SELECT kind FROM contributors WHERE id=?", (viewer,)).fetchone()
            return bool(r) and r[0] in MODERATOR_KINDS
        return False

    def _require_owner(self, c: dict, who: str, action: str) -> None:
        if c["scope"] == "personal" and c["owner_id"] != who:
            raise BrainError(f"cannot {action} another person's personal claim")

    def _require_changer(self, c: dict, who: str, action: str) -> None:
        """Who may add evidence or links to a claim:
          personal          -> its owner
          shared_candidate  -> its owner (supporting the proposal) or a moderator
          shared            -> moderators only (everyone else: propose_correction)"""
        self._require_owner(c, who, action)
        if c["scope"] == "shared_candidate" and c["owner_id"] == who:
            return
        if c["scope"] != "personal" and self._known_contributor(who) not in MODERATOR_KINDS:
            raise BrainError(f"shared knowledge: only moderators can {action} it "
                             "(use propose_correction)")

    def _usable_source(self, source_id: int, who: str) -> Optional[dict]:
        """The source if `who` may cite it: it exists and is not someone
        else's private source. Same answer for both refusals, so a private
        source's existence is never revealed."""
        src = self.source(source_id)
        if src is None or (src["private"] and src["added_by"] != who):
            return None
        return src

    def _usable_section(self, section_id: int, who: str) -> bool:
        r = self._db.execute("SELECT source_id FROM sections WHERE id=?", (section_id,)).fetchone()
        return r is not None and self._usable_source(r[0], who) is not None

    # -- sources and sections ----------------------------------------------------
    @_atomic
    def add_source(self, kind: str, title: str = "", reference: str = "", by: Optional[str] = None,
                   content_hash: Optional[str] = None, captured_at: Optional[str] = None,
                   private: bool = False, raw_text: Optional[str] = None,
                   meta: Optional[dict] = None) -> int:
        if kind not in SOURCE_KINDS:
            raise BrainError(f"unknown source kind {kind!r}")
        if self.role == "shared":
            raise BrainError("sources reach the shared file only through an owner's explicit "
                             "permission (approved proposals)")
        by = by or self.local_user
        with self._lock:
            self._known_contributor(by)
            cur = self._new("sources", "INSERT INTO main.sources (kind, title, reference, content_hash, captured_at, added_at, "
                "added_by, private, raw_text, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (kind, title, reference, content_hash, captured_at, _now(), by, int(private),
                 raw_text, json.dumps(meta or {}, ensure_ascii=False)))
            return cur.lastrowid

    @_atomic
    def add_section(self, source_id: int, text: str, heading_path: str = "",
                    position: int = 0, by: Optional[str] = None) -> int:
        by = by or self.local_user
        with self._lock:
            src = self.source(source_id)
            if src is None or src["added_by"] != by:
                raise BrainError(f"no source {source_id} of yours")
            cur = self._new("sections", "INSERT INTO main.sections (source_id, heading_path, position, text) VALUES (?,?,?,?)",
                (source_id, heading_path, position, text))
            return cur.lastrowid

    def source(self, source_id: int) -> Optional[dict]:
        with self._lock:
            r = self._db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(r) if r else None

    # -- claims ------------------------------------------------------------------
    @_atomic
    def add_claim(self, text: str, origin: str, by: Optional[str] = None, subject: str = "",
                  category: str = "", section_id: Optional[int] = None,
                  status: str = "submitted", scope: str = "personal") -> int:
        if origin not in ORIGINS:
            raise BrainError(f"unknown origin {origin!r}")
        if status not in ("submitted", "awaiting_verification"):
            raise BrainError("new claims start as 'submitted' or 'awaiting_verification' "
                             "(verification is a separate, evidence-based review)")
        if scope not in SCOPES:
            raise BrainError(f"unknown scope {scope!r}")
        if scope != "personal":
            raise BrainError("new claims are personal; sharing is a separate, explicit step")
        if not (text or "").strip():
            raise BrainError("a claim needs text")
        by = by or self.local_user
        now = _now()
        if self.role == "shared":
            raise BrainError("shared knowledge is added only by approving a proposal")
        with self._lock:
            kind = self._known_contributor(by)
            if self.role == "personal" and by != self.owner and kind not in AUTOMATIC_KINDS:
                raise BrainError("this personal file belongs to someone else")
            if section_id is not None and not self._usable_section(section_id, by):
                raise BrainError(f"no section {section_id}")
            cur = self._new("claims", "INSERT INTO main.claims (text, subject, category, origin, status, scope, owner_id, "
                "section_id, version, created, created_by, updated) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                (text.strip(), subject, category, origin, status, scope,
                 self.owner if self.role == "personal" else by, section_id, now, by, now))
            cid = cur.lastrowid
            self._xref("claims", cid, "section_id", "sections", section_id)
            self._index_claim(cid, text.strip(), subject, category)
            self._event(cid, "created", None, status, by, f"origin={origin}")
            return cid

    def _claim(self, claim_id: int) -> Optional[dict]:
        """Internal: no visibility check (use claim() for anything a person sees)."""
        r = self._db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        return dict(r) if r else None

    def claim(self, claim_id: int, viewer: Optional[str] = None) -> Optional[dict]:
        """The claim if `viewer` (default: the local user) may see it, else None.
        In a session, `withdrawn_support` is True for a shared claim that is
        verified but no longer presented so, because its source's owner
        withdrew the evidence it rested on (B5.2)."""
        with self._lock:
            c = self._claim(claim_id)
            if not self.can_see(c, viewer):
                return None
            if self.attached:
                from . import sources as B6
                raw = self._db.execute("SELECT status FROM shared.claims WHERE id=?",
                                       (claim_id,)).fetchone()
                changed = B6.source_changed(self, claim_id)
                c["withdrawn_support"] = bool(raw and raw[0] != c["status"]) and \
                    bool(self.withdrawn_evidence(claim_id))
                c["source_changed"] = changed
            return c

    def claims(self, subject: Optional[str] = None, status: Optional[str] = None,
               text_like: Optional[str] = None, limit: int = 200,
               viewer: Optional[str] = None) -> List[dict]:
        """Claims the viewer may see: shared ones + their own (+ candidates
        for moderators). Filtering happens in the database query itself."""
        with self._lock:
            vis, args = self._visible_sql(viewer, "claims")
        q = f"SELECT * FROM claims WHERE {vis}"
        if subject is not None:
            q += " AND lower(subject)=lower(?)"
            args.append(subject)
        if status is not None:
            q += " AND status=?"
            args.append(status)
        if text_like:
            q += " AND (lower(text) LIKE ? OR lower(subject) LIKE ?)"
            args += [f"%{text_like.lower()}%"] * 2
        q += " ORDER BY id LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._db.execute(q, args)]

    def _event(self, claim_id: int, event: str, old: Optional[str], new: Optional[str],
               by: str, note: str = "", related: Optional[int] = None) -> None:
        cur = self._new("claim_events", "INSERT INTO main.claim_events (claim_id, event, old_status, "
                        "new_status, by, at, note, related_claim) VALUES (?,?,?,?,?,?,?,?)",
                        (claim_id, event, old, new, by, _now(), note, related))
        self._xref("claim_events", cur.lastrowid, "claim_id", "claims", claim_id)
        self._xref("claim_events", cur.lastrowid, "related_claim", "claims", related)

    def history(self, claim_id: int, viewer: Optional[str] = None) -> Dict[str, list]:
        """Events and earlier wordings. What happened while the claim was
        personal (its old wordings, correction notes, evidence added) stays
        with its owner: others only see history from the moment it was
        proposed for sharing (claims are only corrected while personal)."""
        with self._lock:
            c = self._claim(claim_id)
            if not self.can_see(c, viewer):
                return {"events": [], "versions": []}
            owner = c["owner_id"] == (viewer or self.local_user)
            since = 0
            if not owner:                    # non-owners only see claims that were proposed
                r = self._db.execute("SELECT MIN(id) FROM claim_events WHERE claim_id=? AND "
                                     "event='share_proposed'", (claim_id,)).fetchone()[0]
                since = r if r is not None else 2 ** 62
            ev = []
            for r in self._db.execute("SELECT * FROM claim_events WHERE claim_id=? AND id>=? "
                                      "ORDER BY id", (claim_id, since)).fetchall():
                e = dict(r)
                # events about another claim are shown only if that claim is
                # visible too (never its id, wording, relation or author)
                rel = e.get("related_claim")
                refs = [rel] if rel is not None else \
                    [int(x) for x in re.findall(r"claim (\d+)", e.get("note") or "")]
                if all(self.can_see(self._claim(x), viewer) for x in refs):
                    ev.append(e)
            ver = [dict(r) for r in self._db.execute(
                "SELECT * FROM claim_versions WHERE claim_id=? ORDER BY version", (claim_id,))] \
                if owner else []
        return {"events": ev, "versions": ver}

    # -- evidence ------------------------------------------------------------------
    @_atomic
    def add_evidence(self, claim_id: int, kind: str, source_id: Optional[int] = None,
                     locator: str = "", by: Optional[str] = None, _located: bool = False) -> int:
        if kind not in EVIDENCE_KINDS:
            raise BrainError(f"unknown evidence kind {kind!r}")
        if kind in ("direct", "confirmation") and source_id is None:
            raise BrainError("direct evidence / confirmations must point to a source")
        by = by or self.local_user
        with self._lock:
            self._known_contributor(by)
            c = self._claim(claim_id)
            if c is None or not self.can_see(c, by):
                raise BrainError(f"no claim {claim_id}")
            self._require_changer(c, by, "add evidence to")
            if self.attached and not self._db.execute("SELECT 1 FROM main.claims WHERE id=?",
                                                      (claim_id,)).fetchone():
                raise BrainError("shared knowledge: evidence is added by moderators")
            if source_id is not None:
                src = self._usable_source(source_id, by)
                if src is None:
                    raise BrainError(f"no source {source_id} (evidence must be traceable)")
                if src["private"] and c["scope"] != "personal":
                    raise BrainError("a shared / proposed claim cannot get evidence from a "
                                     "private source")
                from . import sources as B6
                if not _located and B6.is_imported(self, source_id):
                    raise BrainError("quotations from an imported source are added with cite(), "
                                     "which checks they are in the source exactly")
            if kind == "derived" and not self._db.execute(
                    "SELECT 1 FROM claim_links WHERE from_claim=? AND kind='derived_from'",
                    (claim_id,)).fetchone():
                raise BrainError("derived evidence needs a 'derived_from' link to the claims "
                                 "it comes from")
            cur = self._new("evidence", "INSERT INTO main.evidence (claim_id, source_id, kind, locator, added_by, added_at) "
                "VALUES (?,?,?,?,?,?)", (claim_id, source_id, kind, locator, by, _now()))
            self._xref("evidence", cur.lastrowid, "source_id", "sources", source_id)
            self._event(claim_id, "evidence_added", None, None, by,
                        f"{kind}" + (f" from source {source_id}" if source_id else ""))
            return cur.lastrowid

    def evidence(self, claim_id: int) -> List[dict]:
        """Evidence rows of a claim. Evidence its source's owner WITHDREW (B5.2)
        is left out: it is no support and nothing to show."""
        with self._lock:
            gone = self.withdrawn_evidence(claim_id)
            return [dict(r) for r in self._db.execute(
                "SELECT e.*, s.kind AS source_kind, s.content_hash, s.private, s.title, "
                "s.reference FROM evidence e LEFT JOIN sources s ON s.id = e.source_id "
                "WHERE e.claim_id=? ORDER BY e.id", (claim_id,)) if r["id"] not in gone]

    def evidence_summary(self, claim_id: int) -> dict:
        """How well supported a claim is. The same material (same content
        hash) counts once, however many people submit it; assertions are
        counted separately and never count as support."""
        from . import sources as B6
        ev = self.evidence(claim_id)
        direct, confirm = set(), set()
        statements = 0
        claim = self._claim(claim_id)
        imported = {"direct": [], "confirmation": []}    # B6: (key, version) that count
        not_evidence = 0
        for e in ev:
            key = e["content_hash"] or f"source:{e['source_id']}"
            if e["kind"] in ("direct", "confirmation") and e["source_kind"] == "user_statement":
                statements += 1                  # someone's own words: never support
            elif e["kind"] in ("direct", "confirmation") and B6.is_imported(self, e["source_id"]):
                ok, _ = B6.evidence_check(self, e["id"], claim, strict=False)
                if ok:                           # B6: exact, current, not self / AI
                    imported[e["kind"]].append((key, B6.evidence_info(self, e["id"])))
                else:
                    not_evidence += 1
            elif e["kind"] == "direct":
                direct.add(key)
            elif e["kind"] == "confirmation":
                confirm.add(key)
        attested = 0
        for key, v in imported["direct"]:
            direct.add(key)
            attested += v is not None and v["provenance"] == "attested"
        # B6 independence: a confirmation from an imported source counts only if
        # it and a direct source are both ATTESTED, with different publishers and
        # different material (an unknown author never confirms anything)
        good_direct = [v for _, v in imported["direct"] if v and v["provenance"] == "attested"
                       and (v["publisher"] or "").strip()]
        for key, v in imported["confirmation"]:
            if not v or v["provenance"] != "attested" or v["author_kind"] != "named" or \
                    not (v["publisher"] or "").strip():
                continue
            if any(d["publisher"].strip().lower() != v["publisher"].strip().lower() and
                   d["text_tag"] != v["text_tag"] for d in good_direct):
                confirm.add(key)
        confirm -= direct
        return {"direct": len(direct), "confirmations": len(confirm),
                "imported": len(imported["direct"]) + len(imported["confirmation"]),
                "attested": attested, "not_evidence": not_evidence,
                "legacy": sum(1 for e in ev if e["kind"] in ("direct", "confirmation") and
                              e["source_kind"] != "user_statement" and
                              not B6.is_imported(self, e["source_id"])),
                "derived": sum(1 for e in ev if e["kind"] == "derived"),
                "assertions": sum(1 for e in ev if e["kind"] == "assertion") + statements,
                "contributors": len({e["added_by"] for e in ev}),
                "private_sources": sum(1 for e in ev if e["private"])}

    def cited_evidence(self, claim_id: int, viewer: Optional[str] = None) -> List[dict]:
        """Evidence of a claim that `viewer` may be shown as a citation (B4).
        Read-only. Nothing for a claim the viewer's answers may not use (the
        search rule: shared or their own). A private source is cited only on
        its owner's own personal claim - never on shared knowledge (a shared
        answer must be safe to pass on) and never to anyone else.

        B6 (presentation only, nothing new is revealed): `page` - the PDF page of
        a quotation located in a version the viewer may read, else None;
        `withheld` - True when the source is a shared copy whose owner did not
        grant its details (title / reference / date). Only the source's own
        date (captured_at) is given - never when a copy was made."""
        viewer = viewer or self.local_user
        with self._lock:
            c = self._claim(claim_id)
            if not self.searchable(c, viewer):
                return []
            gone = self.withdrawn_evidence(claim_id)
            rows = [dict(r) for r in self._db.execute(
                "SELECT e.id AS ev_id, e.kind, e.locator, s.id AS source_id, s.kind AS source_kind, "
                "s.title, s.captured_at, s.private, s.added_by "
                "FROM evidence e LEFT JOIN sources s ON s.id = e.source_id "
                "WHERE e.claim_id=? ORDER BY e.id", (claim_id,)) if r["ev_id"] not in gone]
            for r in rows:
                r["evidence_id"] = r.pop("ev_id")
                r["page"] = self._cited_page(r["evidence_id"])
                r["withheld"] = self._withheld(r["source_id"], r["title"])
                r["personal_source"] = self._personal_details(c, r)
        out = []
        for r in rows:
            if r["source_id"] is not None and r["private"] and \
                    (r["added_by"] != viewer or c["scope"] != "personal"):
                continue                       # private: only on the owner's personal claim
            r.pop("added_by")
            out.append(r)
        return out

    def _personal_details(self, claim: dict, row: dict) -> bool:
        """B6.1: True when a SHARED claim is cited with a source title / date taken from the
        viewer's OWN full row of that source. A library migrated from one file keeps the
        owner's full row under the SAME id as the shared stub (whose details were never
        granted), and the union view lets the owner's row win: the owner sees their title and
        date, and a response carrying them must not be marked shared."""
        if claim["scope"] != "shared" or self.role != "personal" or row["source_id"] is None:
            return False
        if not (row["title"] or row["captured_at"]):
            return False
        return self._db.execute("SELECT 1 FROM main.sources WHERE id=?",
                                (row["source_id"],)).fetchone() is not None

    def _cited_page(self, evidence_id: int) -> Optional[int]:
        """B6: the PDF page of a located quotation (None if none / not a PDF)."""
        if self.role == "file":
            return None
        r = self._db.execute("SELECT l.start_pos, l.end_pos, v.fmt, v.structure FROM "
                             "evidence_locations l JOIN source_versions v ON v.id=l.version_id "
                             "WHERE l.evidence_id=?", (evidence_id,)).fetchone()
        if r is None or r["fmt"] != "pdf":
            return None
        from .pdf_import import page_of
        pg = page_of(r["structure"], r["start_pos"], r["end_pos"])
        return pg["page"] if pg else None

    def _withheld(self, source_id: Optional[int], title: Optional[str]) -> bool:
        """B6: a shared copy of someone's source whose details were not granted."""
        sh = self._shared_schema()
        if source_id is None or title is not None or sh is None:
            return False
        return self._db.execute(f"SELECT 1 FROM {sh}.source_opaque WHERE source_id=?",
                                (source_id,)).fetchone() is not None

    # -- review (the only way to 'verified' / 'rejected' / ...) ---------------------
    @_atomic
    def review(self, claim_id: int, new_status: str, reviewer: Optional[str] = None,
               note: str = "") -> None:
        if new_status not in STATUSES:
            raise BrainError(f"unknown status {new_status!r}")
        reviewer = reviewer or self.local_user
        with self._lock:
            rkind = self._known_contributor(reviewer)
            c = self._claim(claim_id)
            if c is None or not self.can_see(c, reviewer):
                raise BrainError(f"no claim {claim_id}")
            self._require_owner(c, reviewer, "review")
            if c["scope"] != "personal" and rkind not in MODERATOR_KINDS:
                raise BrainError("shared / proposed knowledge is reviewed by moderators")
            if new_status == "verified":
                s = self.evidence_summary(claim_id)
                if s["direct"] + s["confirmations"] == 0:
                    raise BrainError("cannot verify: no direct evidence or independent "
                                     "confirmation (repeated statements don't count)")
                if self.role == "shared" and not any(
                        e["kind"] in ("direct", "confirmation") and (e["locator"] or "").strip()
                        for e in self.evidence(claim_id)):
                    raise BrainError("cannot verify shared knowledge without a quote its owner "
                                     "allowed to be shared")
                if self.role == "shared" and s["imported"] and not s["legacy"] and \
                        not s["attested"]:
                    raise BrainError("cannot verify shared knowledge resting only on imported "
                                     "sources until a moderator attests one source's authorship "
                                     "(attestation is not a check of the facts)")
                if rkind in AUTOMATIC_KINDS:
                    game = [e for e in self.evidence(claim_id) if e["kind"] == "direct"
                            and e["source_kind"] == "game_observation"]
                    if c["origin"] != "observed" or not game:
                        raise BrainError("no automatic verification: a person must review it "
                                         "(only SK's own direct game observation is exempt)")
                # only contradictions the claim's WHOLE audience can see count:
                # a hidden (e.g. private) claim must not influence the review
                others = self._db.execute(
                    "SELECT o.* FROM claim_links l JOIN claims o ON o.id = "
                    "CASE WHEN l.from_claim=? THEN l.to_claim ELSE l.from_claim END "
                    "WHERE l.kind='contradicts' AND (l.from_claim=? OR l.to_claim=?) "
                    "AND o.status NOT IN ('rejected','outdated')",
                    (claim_id, claim_id, claim_id)).fetchall()
                open_conflict = any(self._same_audience(c, dict(o)) for o in others)
                if open_conflict:
                    raise BrainError("cannot verify: a contradicting claim is still open "
                                     "(reject or mark it outdated first)")
            self._db.execute("UPDATE main.claims SET status=?, updated=? WHERE id=?",
                             (new_status, _now(), claim_id))
            self._event(claim_id, "reviewed", c["status"], new_status, reviewer, note)

    # -- corrections: new version, old wording kept --------------------------------
    @_atomic
    def correct(self, claim_id: int, new_text: str, by: Optional[str] = None, reason: str = "",
                subject: Optional[str] = None, category: Optional[str] = None) -> int:
        """Replace the wording; the previous wording is stored as a version.
        A corrected claim goes back to 'awaiting_verification' (the new
        wording has not been checked yet). Returns the new version number."""
        by = by or self.local_user
        if not (new_text or "").strip():
            raise BrainError("a correction needs text")
        with self._lock:
            self._known_contributor(by)
            c = self._claim(claim_id)
            if c is None or not self.can_see(c, by):
                raise BrainError(f"no claim {claim_id}")
            self._require_owner(c, by, "correct")
            if c["scope"] != "personal":
                raise BrainError("shared / proposed knowledge is not edited in place: use "
                                 "propose_correction()")
            self._new("claim_versions", "INSERT INTO main.claim_versions (claim_id, version, text, subject, category, "
                "old_status, replaced_by, replaced_at, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                (claim_id, c["version"], c["text"], c["subject"], c["category"], c["status"],
                 by, _now(), reason))
            v = c["version"] + 1
            self._db.execute(
                "UPDATE main.claims SET text=?, subject=?, category=?, version=?, status=?, updated=? "
                "WHERE id=?", (new_text.strip(), c["subject"] if subject is None else subject,
                               c["category"] if category is None else category, v,
                               "awaiting_verification", _now(), claim_id))
            n = self._claim(claim_id)
            self._index_claim(claim_id, n["text"], n["subject"], n["category"])
            self._event(claim_id, "corrected", c["status"], "awaiting_verification", by,
                        reason or f"version {v}")
            return v

    @_atomic
    def propose_correction(self, claim_id: int, new_text: str, by: Optional[str] = None,
                           reason: str = "") -> int:
        """A correction to knowledge the person doesn't own (e.g. shared): a
        new personal claim of theirs, linked 'supersedes' -> the original,
        which stays unchanged until reviewed. Returns the new claim id."""
        by = by or self.local_user
        with self._lock:
            c = self._claim(claim_id)
            if c is None or not self.can_see(c, by):
                raise BrainError(f"no claim {claim_id}")
            sec = c["section_id"] if c["section_id"] is not None and \
                self._usable_section(c["section_id"], by) else None
            new = self.add_claim(new_text, "user", by, c["subject"], c["category"], sec)
            lc = self._new("claim_links", "INSERT INTO main.claim_links (from_claim, to_claim, kind, "
                           "added_by, added_at) VALUES (?,?,'supersedes',?,?)",
                           (new, claim_id, by, _now()))
            self._xref("claim_links", lc.lastrowid, "to_claim", "claims", claim_id)
            self._event(new, "correction_proposed", None, None, by,
                        f"for claim {claim_id}: {reason}", related=claim_id)
            self._event(claim_id, "correction_received", None, None, by, f"claim {new}",
                        related=new)
            return new

    # -- relations -------------------------------------------------------------------
    @_atomic
    def link(self, from_claim: int, to_claim: int, kind: str, by: Optional[str] = None) -> None:
        if kind not in ("supports", "contradicts", "derived_from", "supersedes", "duplicate_of"):
            raise BrainError(f"unknown link kind {kind!r}")
        by = by or self.local_user
        with self._lock:
            self._known_contributor(by)
            a, b = self._claim(from_claim), self._claim(to_claim)
            if not (self.can_see(a, by) and self.can_see(b, by)):
                raise BrainError("both claims must exist and be visible to you")
            self._require_changer(a, by, "link")
            if self.attached and not self._db.execute("SELECT 1 FROM main.claims WHERE id=?",
                                                      (from_claim,)).fetchone():
                raise BrainError("shared knowledge: links are added by moderators")
            lc = self._new("claim_links", "INSERT OR IGNORE INTO main.claim_links (from_claim, to_claim, kind, "
                             "added_by, added_at) VALUES (?,?,?,?,?)",
                             (from_claim, to_claim, kind, by, _now()))
            if lc.rowcount:
                self._xref("claim_links", lc.lastrowid, "to_claim", "claims", to_claim)
            self._event(from_claim, f"link_{kind}", None, None, by, f"-> claim {to_claim}",
                        related=to_claim)
            if kind == "contradicts":
                # both sides become 'conflicting' (unless already rejected / outdated) -
                # but a claim is only flagged if everyone who can see it can also
                # see the claim contradicting it (otherwise its status alone would
                # reveal that someone's private claim exists)
                for cid, oid in ((from_claim, to_claim), (to_claim, from_claim)):
                    c, o = self._claim(cid), self._claim(oid)
                    if not c or c["status"] in ("rejected", "outdated", "conflicting"):
                        continue
                    if not self._same_audience(c, o):
                        continue
                    self._db.execute("UPDATE main.claims SET status='conflicting', updated=? "
                                     "WHERE id=?", (_now(), cid))
                    self._event(cid, "conflict_flagged", c["status"], "conflicting", by,
                                f"contradicted by claim {oid}", related=oid)

    def _same_audience(self, c: dict, other: dict) -> bool:
        """Can everyone who may see claim c also see claim `other`?
          personal          -> its owner
          shared_candidate  -> its owner and every moderator
          shared            -> everyone"""
        if other is None:
            return False
        if c["scope"] == "shared":
            return other["scope"] == "shared"
        if c["scope"] == "shared_candidate":
            return other["scope"] == "shared" or (other["scope"] == "shared_candidate"
                                                  and other["owner_id"] == c["owner_id"])
        return self.can_see(other, c["owner_id"])

    def links(self, claim_id: int, viewer: Optional[str] = None) -> List[dict]:
        """Links of a claim - only those whose BOTH ends the viewer may see."""
        with self._lock:
            if not self.can_see(self._claim(claim_id), viewer):
                return []
            out = []
            for r in self._db.execute(
                    "SELECT * FROM claim_links WHERE from_claim=? OR to_claim=? ORDER BY id",
                    (claim_id, claim_id)).fetchall():
                other = r["to_claim"] if r["from_claim"] == claim_id else r["from_claim"]
                if self.can_see(self._claim(other), viewer):
                    out.append(dict(r))
            return out

    # -- scope: personal -> shared candidate (explicit, recorded) ----------------
    @_atomic
    def propose_share(self, claim_id: int, by: Optional[str] = None) -> None:
        if self.role != "file":
            raise BrainError("in a library, proposals go to the owner's outbox "
                             "(Session.propose) - nothing changes scope in place")
        by = by or self.local_user
        with self._lock:
            c = self._claim(claim_id)
            if c is None or c["owner_id"] != by:
                raise BrainError("only the owner can propose their claim for sharing")
            if c["scope"] != "personal":
                raise BrainError("already proposed / shared")
            if self.evidence_summary(claim_id)["private_sources"]:
                raise BrainError("this claim has evidence from a private source; it cannot be "
                                 "shared (add non-private evidence or remove it first)")
            self._db.execute("UPDATE main.claims SET scope='shared_candidate', updated=? WHERE id=?",
                             (_now(), claim_id))
            self._event(claim_id, "share_proposed", c["status"], c["status"], by,
                        f"scope {c['scope']} -> shared_candidate")

    @_atomic
    def approve_share(self, claim_id: int, moderator: str, note: str = "") -> None:
        """Shared-candidate -> shared. Only a moderator, only for verified
        claims without private evidence (the shared library prefers accuracy)."""
        if self.role != "file":
            raise BrainError("in a library, moderators approve outbox proposals "
                             "(ModeratorSession.approve)")
        with self._lock:
            if self._known_contributor(moderator) not in MODERATOR_KINDS:
                raise BrainError("only a moderator can approve shared knowledge")
            c = self._claim(claim_id)
            if c is None or c["scope"] != "shared_candidate":
                raise BrainError("not a proposed claim")
            if c["status"] != "verified":
                raise BrainError("only verified knowledge enters the shared library")
            proposed = self._db.execute("SELECT MAX(id) FROM claim_events WHERE claim_id=? AND "
                                        "event='share_proposed'", (claim_id,)).fetchone()[0]
            mods = tuple(MODERATOR_KINDS)
            if not self._db.execute(
                    "SELECT 1 FROM claim_events e JOIN contributors k ON k.id = e.by "
                    "WHERE e.claim_id=? AND e.id>? AND e.event='reviewed' AND "
                    f"e.new_status='verified' AND k.kind IN ({','.join('?' * len(mods))})",
                    (claim_id, proposed or 0) + mods).fetchone():
                raise BrainError("a moderator must verify the proposal itself (the owner's "
                                 "own verification is not enough for the shared library)")
            if self.evidence_summary(claim_id)["private_sources"]:
                raise BrainError("has evidence from a private source")
            self._db.execute("UPDATE main.claims SET scope='shared', updated=? WHERE id=?",
                             (_now(), claim_id))
            self._event(claim_id, "share_approved", c["status"], c["status"], moderator, note)

    def stats(self, viewer: Optional[str] = None) -> dict:
        """Counts of the claims this viewer may see (never other people's)."""
        with self._lock:
            out = {s: 0 for s in STATUSES}
            for c in self.claims(limit=10 ** 9, viewer=viewer):
                out[c["status"]] += 1
            # only the viewer's own sources (never other people's activity)
            out["sources"] = self._db.execute("SELECT COUNT(*) FROM sources WHERE added_by=?",
                                              (viewer or self.local_user,)).fetchone()[0]
            out["claims"] = sum(out[s] for s in STATUSES)
        return out

