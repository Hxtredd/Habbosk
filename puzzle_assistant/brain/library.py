"""SK Brain library (B5.2): personal and shared knowledge in SEPARATE files.

    lib = Library(root, host_key=..., library_key=...)       # the host application
    uid = lib.create_user("Ana")                             # once
    s = lib.open_session(mint_grant(host_key, lib, uid))     # one person's session
    s.respond("Who maintains the Lantern Garden?")

Layout (root = %APPDATA%\\SK Assistant\\brain on Windows):
    registry.db             users, file ids, pseudonyms, id counter, used grants -
                            no knowledge
    shared.db               approved shared knowledge only (users: read-only)
    personal/<file>.db      ONE person's knowledge; only their session opens it
    personal/<file>.hwm     highest ids that file used (no knowledge): ids stay
                            unique even if registry.db is restored from a backup
    outbox/<file>.db        what that person explicitly proposed (owner writes,
                            moderators read)
    epoch.json              raised when registry.db is found restored

Guarantees (tests/test_brain_b52.py):
  * a session holds its own personal file (read-write) and shared.db
    (attached READ-ONLY); another person's file is never opened;
  * the session's identity comes from a signed, single-use, short-lived
    grant made by the HOST with its key: this authenticates the host's
    decision, NOT the human (that needs a later authentication milestone);
  * nothing personal reaches shared.db except what the owner explicitly put
    in their outbox and a moderator approved; source material only at the
    level the owner granted; personal verification never carries over (an
    approved copy starts unverified; only a moderator's review verifies it);
  * an owner can withdraw granted material at any time: it is blocked first
    (durably, in shared.db), then physically removed (secure delete, logs
    emptied); evidence that is withdrawn never supports an answer.
No AI, no network. Nothing here touches habbosk.db, atlas.db or knowledge.db.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Dict, List, Optional

from .model import AUTOMATIC_KINDS, MODERATOR_KINDS
from .schema import LAYOUT, OUTBOX_B6_SCHEMA
from .store import (ALLOCATED, BrainError, KnowledgeStore, NeedsUpgrade, _now, file_uri,
                    library_pragmas)

GRANT_TTL = 60            # seconds a grant may live
CLOCK_SKEW = 30           # seconds of clock difference accepted
NONCE_KEEP = 24 * 3600    # used grant values are remembered this long
ID_BLOCK = 32             # ids reserved at a time (a crash only leaves a gap)
USER_KINDS = ("local_user", "external_user", "moderator")
LEVELS = ("quote", "details", "full_text")

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,            -- moderators only ('' for everyone else)
    kind TEXT NOT NULL,
    contributor_id TEXT NOT NULL UNIQUE,   -- the person's id inside their personal file
    pseudonym TEXT NOT NULL UNIQUE,        -- the only id shared storage ever sees
    file_id TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (tbl TEXT PRIMARY KEY, next INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS grants_used (
    nonce TEXT PRIMARY KEY, user_id TEXT NOT NULL, used_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, event TEXT NOT NULL, user_id TEXT, detail TEXT);
"""

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS proposals (
    token TEXT PRIMARY KEY,               -- random
    claim_id INTEGER NOT NULL,            -- the owner's personal claim (never shown)
    text TEXT NOT NULL, subject TEXT, category TEXT, origin TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS material (
    id INTEGER PRIMARY KEY,
    token TEXT NOT NULL,
    kind TEXT NOT NULL,                   -- direct | confirmation | assertion
    opaque_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    tag TEXT,                             -- keyed tag of the content (never a plain hash)
    token_hash TEXT NOT NULL,             -- keyed hash of the withdrawal token
    quote TEXT, title TEXT, reference TEXT, captured_at TEXT, full_text TEXT,
    levels TEXT NOT NULL
);
"""


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_grant(host_key: bytes, installation_id: str, epoch: int, user_id: str, role: str,
               ttl: int = GRANT_TTL, now: Optional[float] = None) -> str:
    """HOST side: a signed, single-use, short-lived permission to open ONE
    person's session. It says the host decided so - not who the human is."""
    now = time.time() if now is None else now
    body = json.dumps({"v": 1, "inst": installation_id, "epoch": epoch, "user": user_id,
                       "role": role, "iat": int(now), "exp": int(now + min(ttl, GRANT_TTL)),
                       "nonce": secrets.token_hex(16)}, sort_keys=True, separators=(",", ":"))
    mac = hmac.new(host_key, body.encode(), hashlib.sha256).digest()
    return _b64(body.encode()) + "." + _b64(mac)


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def checkpoint(con, tries: int = 50, wait: float = 0.05) -> bool:
    """Empty the write-ahead log completely (it may hold withdrawn content).
    False if a reader kept it busy every time (the caller retries later)."""
    for _ in range(tries):
        busy, _log, _done = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if not busy:
            return True
        time.sleep(wait)
    return False


class Library:
    def __init__(self, root: str, host_key: bytes, library_key: bytes):
        if not host_key or len(host_key) < 32 or not library_key or len(library_key) < 32:
            raise BrainError("the host key and the library key must be at least 32 bytes")
        self.root = os.path.abspath(root)
        self.host_key = bytes(host_key)
        self.library_key = bytes(library_key)
        self.crash_at = os.environ.get("SK_TEST_CRASH_AT", "")     # tests: simulated power loss

        self._lock = threading.RLock()
        self._blocks: Dict[str, List[int]] = {}
        from .upgrade import UpgradePending, unfinished
        if unfinished(os.path.dirname(self.root)):
            raise UpgradePending("an upgrade of this library was interrupted - run "
                                 "brain.upgrade.recover_upgrade() first (nothing was opened)")
        reg = self.path("registry.db")
        if os.path.isfile(reg) and os.path.getsize(reg) > 0:      # read-only look first
            # immutable: reads without creating -wal / -shm files next to an old library
            look = sqlite3.connect(file_uri(reg, "ro") + "&immutable=1", uri=True)
            try:
                has = look.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
                layout = look.execute("SELECT value FROM meta WHERE key='layout'").fetchone() \
                    if has else None
            finally:
                look.close()
            if has and layout != (LAYOUT,):
                raise NeedsUpgrade("this library is in an older format (B5.2); it was not opened "
                                   "and nothing in it was changed - upgrade a copy with "
                                   "brain.upgrade.upgrade_library()")
        for d in ("", "personal", "outbox"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        self._reg = sqlite3.connect(file_uri(reg, "rwc"), uri=True,
                                    check_same_thread=False, timeout=5)
        library_pragmas(self._reg)
        self._reg.execute("PRAGMA journal_mode = WAL")
        self._reg.executescript(REGISTRY_SCHEMA)
        if self._meta("installation_id") is None:
            self._set_meta("installation_id", uuid.uuid4().hex)
            self._set_meta("epoch", "1")
            self._set_meta("layout", LAYOUT)
        self._reg.commit()
        self._check_epoch()
        if not os.path.exists(self.path("shared.db")):
            KnowledgeStore(self.path("shared.db"), role="shared", allocator=self.allocator()).close()

    # -- paths, registry -----------------------------------------------------------
    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    # every use of the registry connection holds the library's lock (sessions in
    # several threads share one Library)
    def _meta(self, key: str) -> Optional[str]:
        with self._lock:
            r = self._reg.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._reg.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    @property
    def installation_id(self) -> str:
        return self._meta("installation_id")

    @property
    def epoch(self) -> int:
        return int(self._meta("epoch"))

    def _log(self, event: str, user_id: Optional[str] = None, detail: str = "") -> None:
        with self._lock:
            self._reg.execute("INSERT INTO log (at, event, user_id, detail) VALUES (?,?,?,?)",
                              (_now(), event, user_id, detail))

    def _check_epoch(self) -> None:
        """registry.db restored from an older copy? Its epoch is behind the one in
        epoch.json: raise it, so every grant made before the restore is refused."""
        f = self.path("epoch.json")
        on_file = 0
        if os.path.exists(f):
            try:
                with open(f, encoding="utf-8") as fh:
                    on_file = int(json.load(fh)["epoch"])
            except (OSError, ValueError, KeyError):
                on_file = 0
        reg = self.epoch
        if on_file > reg:
            reg = on_file + 1
            self._set_meta("epoch", str(reg))
            self._log("registry_restored", detail=f"epoch raised to {reg}")
            self._reg.commit()
        if on_file != reg:
            _atomic_write(f, json.dumps({"epoch": reg}).encode())

    def users(self) -> List[dict]:
        with self._lock:
            cur = self._reg.execute("SELECT user_id, display_name, kind, file_id FROM users "
                                    "ORDER BY created, user_id").fetchall()
        return [dict(zip(("user_id", "display_name", "kind", "file_id"), r)) for r in cur]

    def _user(self, user_id: str) -> dict:
        with self._lock:
            r = self._reg.execute("SELECT user_id, display_name, kind, contributor_id, "
                                  "pseudonym, file_id FROM users WHERE user_id=?",
                                  (user_id,)).fetchone()
        if r is None:
            raise BrainError("unknown user")
        return dict(zip(("user_id", "display_name", "kind", "contributor_id", "pseudonym",
                         "file_id"), r))

    def create_user(self, display_name: str, kind: str = "local_user", *,
                    contributor_id: Optional[str] = None, pseudonym: Optional[str] = None,
                    user_id: Optional[str] = None) -> str:
        """A person with their own personal file and outbox (empty)."""
        if kind not in USER_KINDS:
            raise BrainError(f"unknown kind of user {kind!r}")
        with self._lock:
            uid = user_id or uuid.uuid4().hex
            cid = contributor_id or str(uuid.uuid4())
            pseud = pseudonym or str(uuid.uuid4())
            fid = uuid.uuid4().hex
            # a person's name stays in their own file; only moderators are named here
            self._reg.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                              (uid, display_name if kind == "moderator" else "", kind, cid,
                               pseud, fid, _now()))
            self._log("user_created", uid)
            self._reg.commit()
            KnowledgeStore(self.path("personal", fid + ".db"), role="personal",
                           owner=(cid, display_name, kind),
                           allocator=self.allocator(fid)).close()
            self._new_outbox(fid, pseud)
            return uid

    def _new_outbox(self, fid: str, pseudonym: str) -> None:
        con = self._outbox(fid, write=True)
        try:
            con.executescript(OUTBOX_SCHEMA)
            con.executescript(OUTBOX_B6_SCHEMA)
            con.execute("INSERT OR IGNORE INTO meta VALUES ('role', 'outbox')")
            con.execute("INSERT OR IGNORE INTO meta VALUES ('layout', ?)", (LAYOUT,))
            con.execute("INSERT OR IGNORE INTO meta VALUES ('pseudonym', ?)", (pseudonym,))
            con.commit()
        finally:
            con.close()

    def _outbox(self, fid: str, write: bool):
        con = sqlite3.connect(file_uri(self.path("outbox", fid + ".db"), "rwc" if write else "ro"),
                              uri=True, timeout=5)
        library_pragmas(con)
        if write:
            con.execute("PRAGMA journal_mode = WAL")
        return con

    # -- ids: unique across every file of this library --------------------------------
    def allocator(self, file_id: Optional[str] = None):
        """ids for one file. They come from the registry counter (kept above
        every file's recorded high-water mark, so a restored registry can't hand
        out an id a personal file already uses - even one that is not open)."""
        def alloc(table: str) -> int:
            with self._lock:
                block = self._blocks.get(table)
                if not block or block[0] >= block[1]:
                    block = self._blocks[table] = self._reserve(table)
                    if file_id:
                        self._record_hwm(file_id, table, block[1])
                elif file_id and self._hwm(file_id).get(table, 0) < block[1]:
                    self._record_hwm(file_id, table, block[1])
                n = block[0]
                block[0] += 1
                return n
        return alloc

    def _hwm(self, file_id: str) -> Dict[str, int]:
        try:
            with open(self.path("personal", file_id + ".hwm"), encoding="utf-8") as fh:
                return {k: int(v) for k, v in json.load(fh).items()}
        except (OSError, ValueError):
            return {}

    def _record_hwm(self, file_id: str, table: str, value: int) -> None:
        h = self._hwm(file_id)
        h[table] = max(h.get(table, 0), value)
        _atomic_write(self.path("personal", file_id + ".hwm"), json.dumps(h).encode())

    def _floor(self, table: str) -> int:
        """1 + the highest id ANY file of the library holds (high-water marks of
        personal files, the shared file's own rows)."""
        hi = 0
        for name in os.listdir(self.path("personal")):
            if name.endswith(".hwm"):
                hi = max(hi, self._hwm(name[:-4]).get(table, 0))
        if os.path.exists(self.path("shared.db")):
            con = sqlite3.connect(file_uri(self.path("shared.db"), "ro"), uri=True)
            try:
                hi = max(hi, con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                pass
            finally:
                con.close()
        return hi + 1

    def _reserve(self, table: str) -> List[int]:
        if table not in ALLOCATED:
            raise BrainError(f"no ids for {table!r}")
        self._reg.execute("BEGIN IMMEDIATE")
        try:
            r = self._reg.execute("SELECT next FROM counters WHERE tbl=?", (table,)).fetchone()
            start = max(r[0] if r else 1, self._floor(table))
            self._reg.execute("INSERT OR REPLACE INTO counters VALUES (?,?)",
                              (table, start + ID_BLOCK))
            self._reg.commit()                       # durable before any of these ids is used
        except BaseException:
            self._reg.rollback()
            raise
        return [start, start + ID_BLOCK]

    # -- keyed hashes (never plain document hashes in shared storage) ------------------
    def tag(self, plain: str) -> str:
        return hmac.new(self.library_key, b"tag\0" + plain.encode(), hashlib.sha256).hexdigest()

    def token_hash(self, token: str) -> str:
        return hmac.new(self.library_key, b"tok\0" + token.encode(), hashlib.sha256).hexdigest()

    # -- grants -> sessions ---------------------------------------------------------------
    def check_grant(self, grant: str, now: Optional[float] = None) -> dict:
        """Verify a host grant and mark it used (single use). Raises BrainError."""
        now = time.time() if now is None else now
        try:
            b, m = grant.split(".")
            body, mac = _unb64(b), _unb64(m)
            g = json.loads(body)
        except (ValueError, AttributeError):
            self._refused("malformed")
        if not hmac.compare_digest(hmac.new(self.host_key, body, hashlib.sha256).digest(), mac):
            self._refused("bad signature")
        if g.get("v") != 1 or g.get("inst") != self.installation_id:
            self._refused("another installation")
        if g.get("epoch") != self.epoch:
            self._refused("made before the registry was restored")
        if not (g["iat"] - CLOCK_SKEW <= now <= g["exp"] + CLOCK_SKEW) or \
                g["exp"] - g["iat"] > GRANT_TTL:
            self._refused("expired")
        u = self._user(g["user"])
        role = g.get("role")
        if role not in ("user", "moderator") or (role == "moderator" and u["kind"] != "moderator"):
            self._refused("wrong role")
        with self._lock:
            self._reg.execute("DELETE FROM grants_used WHERE used_at < ?", (now - NONCE_KEEP,))
            try:
                self._reg.execute("INSERT INTO grants_used VALUES (?,?,?)",
                                  (g["nonce"], u["user_id"], now))
            except sqlite3.IntegrityError:
                self._reg.rollback()
                self._refused("already used")
            self._log("session_opened", u["user_id"], role)
            self._reg.commit()
        return {"user": u, "role": role}

    def _refused(self, why: str):
        with self._lock:
            self._log("grant_refused", detail=why)
            self._reg.commit()
        raise BrainError(f"session refused: {why}")

    def open_session(self, grant: str, now: Optional[float] = None):
        g = self.check_grant(grant, now)
        if g["role"] == "moderator":
            return ModeratorSession(self, g["user"])
        return Session(self, g["user"])

    def close(self) -> None:
        self._reg.close()

    # -- withdrawal, step by step (each step is safe to repeat) -------------------------------
    def _crash(self, point: str) -> None:
        if self.crash_at == point:
            os._exit(77)                             # tests: power lost here

    def _shared_writer(self):
        con = sqlite3.connect(file_uri(self.path("shared.db"), "rw"), uri=True, timeout=10)
        library_pragmas(con)
        con.execute("PRAGMA journal_mode = WAL")
        return con

    def block(self, opaque_id: str, token: str) -> None:
        """STEP 1 - the durable intent: from this commit on, every read method
        skips the material (answers, citations, moderators' outbox reads,
        approvals). The token proves ownership."""
        th = self.token_hash(token)
        con = self._shared_writer()
        try:
            con.execute("BEGIN IMMEDIATE")
            known = con.execute("SELECT token_hash FROM source_tokens WHERE opaque_id=?",
                                (opaque_id,)).fetchone()
            # refused unless a token record exists AND the token matches it (the record
            # is registered when the material is first proposed - see register_token)
            if known is None or not hmac.compare_digest(known[0], th):
                con.rollback()
                raise BrainError("not your source")
            con.execute("INSERT OR IGNORE INTO withdrawals VALUES (?,?,'blocked',?,NULL)",
                        (opaque_id, th, _now()))
            self._crash("block_before_commit")
            con.commit()
        finally:
            con.close()

    def register_token(self, opaque_id: str, token_hash: str) -> None:
        """Record the keyed hash of a source's withdrawal token in shared.db when
        its material is first proposed (narrow, insert-only: an existing record
        is never replaced, so nobody can take over another person's source).
        Holds no content and no owner identity."""
        con = self._shared_writer()
        try:
            con.execute("BEGIN IMMEDIATE")
            known = con.execute("SELECT token_hash FROM source_tokens WHERE opaque_id=?",
                                (opaque_id,)).fetchone()
            if known is not None and not hmac.compare_digest(known[0], token_hash):
                con.rollback()
                raise BrainError("this source is registered to someone else")
            con.execute("INSERT OR IGNORE INTO source_tokens VALUES (?,?)",
                        (opaque_id, token_hash))
            con.commit()
        finally:
            con.close()

    def purge_shared(self) -> List[str]:
        """STEPS 4-6 for every blocked withdrawal (any session may finish them):
        the material is securely deleted from shared.db, the log emptied, the
        file checked; then the block is marked complete. Returns the ids still
        pending (a reader kept the log busy)."""
        pending = []
        con = self._shared_writer()
        try:
            for (oid,) in con.execute("SELECT opaque_id FROM withdrawals WHERE state='blocked'"
                                      ).fetchall():
                con.execute("BEGIN IMMEDIATE")
                src = [r[0] for r in con.execute("SELECT source_id FROM source_opaque "
                                                 "WHERE opaque_id=?", (oid,))]
                for sid in src:
                    con.execute("UPDATE sources SET title=NULL, reference=NULL, raw_text=NULL, "
                                "captured_at=NULL, meta='{}' WHERE id=?", (sid,))
                    con.execute("DELETE FROM sections WHERE source_id=?", (sid,))
                    from . import sources as B6                   # B6: versions and passages
                    B6.delete_passages(None, "source_id=?", (sid,), con=con)
                    con.execute("DELETE FROM evidence_locations WHERE evidence_id IN (SELECT id "
                                "FROM evidence WHERE source_id=?)", (sid,))
                    con.execute("DELETE FROM quote_checks WHERE evidence_id IN (SELECT id "
                                "FROM evidence WHERE source_id=?)", (sid,))
                    con.execute("DELETE FROM source_versions WHERE source_id=?", (sid,))
                    for (eid,) in con.execute("SELECT id FROM evidence WHERE source_id=?",
                                              (sid,)).fetchall():
                        con.execute("UPDATE evidence SET locator='' WHERE id=?", (eid,))
                        con.execute("INSERT OR IGNORE INTO evidence_withdrawn VALUES (?,?)",
                                    (eid, _now()))
                con.commit()
                from .sources import scrub_search_index
                scrub_search_index(con)
                con.commit()
                self._crash("purge_after_delete")
                if not checkpoint(con):
                    pending.append(oid)
                    continue
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BrainError("the shared file failed its integrity check")
                self._crash("purge_before_complete")
                con.execute("UPDATE withdrawals SET state='complete', completed_at=? "
                            "WHERE opaque_id=?", (_now(), oid))
                con.commit()
                checkpoint(con)
        finally:
            con.close()
        return pending


def _needles(text) -> List[bytes]:
    """Pieces of a text to look for in backups (a few lines of it)."""
    if not text:
        return []
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) >= 24:
            out.append(line[:60].encode("utf-8"))
        if len(out) >= 8:
            break
    return out


class _Base:
    """What every session shares: a fixed identity, closed files on close()."""

    def __init__(self, lib: Library, user: dict):
        self._lib = lib
        self.user_id = user["user_id"]
        self._user = user
        self._closed = False

    def __reduce__(self):                           # a session cannot be copied or saved
        raise TypeError("a session cannot be saved or copied")

    def _me(self, who: Optional[str]) -> None:
        if who not in (None, "", self._me_id):
            raise BrainError("this session belongs to another person")


class Session(_Base):
    """ONE person: their personal file (read-write) + shared.db (READ-ONLY).
    Reading sees their own knowledge and shared knowledge - nobody else's."""

    def __init__(self, lib: Library, user: dict):
        super().__init__(lib, user)
        fid = user["file_id"]
        self._me_id = user["contributor_id"]
        self.store = KnowledgeStore(lib.path("personal", fid + ".db"), role="personal",
                                    owner=(user["contributor_id"], user["display_name"],
                                           user["kind"]),
                                    shared_path=lib.path("shared.db"),
                                    allocator=lib.allocator(fid))
        from .api import Brain
        self.brain = Brain._over(self.store)
        self.finish_pending()

    # reading / own knowledge: identity fixed - a viewer / by naming anyone else is refused
    def _call(self, name, *a, viewer=None, by=None, **kw):
        self._me(viewer)
        self._me(by)
        fn = getattr(self.brain, name)
        if "viewer" in fn.__code__.co_varnames:
            kw["viewer"] = self._me_id
        if "by" in fn.__code__.co_varnames:
            kw["by"] = self._me_id
        return fn(*a, **kw)

    def respond(self, q, viewer=None):
        return self._call("respond", q, viewer=viewer)

    def ask(self, q, viewer=None):
        return self._call("ask", q, viewer=viewer)

    def search(self, q, viewer=None, **kw):
        return self._call("search", q, viewer=viewer, **kw)

    def claim(self, cid, viewer=None):
        return self._call("claim", cid, viewer=viewer)

    def explain(self, cid, viewer=None):
        return self._call("explain", cid, viewer=viewer)

    def stats(self, viewer=None):
        return self._call("stats", viewer=viewer)

    def learn(self, text, origin="user", subject="", category="", section_id=None, by=None):
        return self._call("learn", text, origin, subject, category, section_id, by=by)

    def add_source(self, kind, by=None, **kw):
        return self._call("add_source", kind, by=by, **kw)

    def add_section(self, source_id, text, heading_path="", position=0, by=None):
        return self._call("add_section", source_id, text, heading_path, position, by=by)

    def add_evidence(self, claim_id, kind, source_id=None, locator="", by=None):
        return self._call("add_evidence", claim_id, kind, source_id, locator, by=by)

    def review(self, claim_id, status, note="", reviewer=None):
        self._me(reviewer)
        return self.brain.review(claim_id, status, self._me_id, note)

    def correct(self, claim_id, new_text, reason="", by=None):
        return self._call("correct", claim_id, new_text, reason, by=by)

    def propose_correction(self, claim_id, new_text, reason="", by=None):
        return self._call("propose_correction", claim_id, new_text, reason, by=by)

    def link(self, a, b, kind, by=None):
        return self._call("link", a, b, kind, by=by)

    def add_relation(self, key, value_type, sentence_forms, question_forms, labels=None, by=None,
                     **kw):
        return self._call("add_relation", key, value_type, sentence_forms, question_forms,
                          labels, by=by, **kw)

    def add_entity(self, name, kind="", by=None):
        return self._call("add_entity", name, kind, "personal", by=by)

    def add_alias(self, entity_id, alias, language="", by=None):
        return self._call("add_alias", entity_id, alias, language, by=by)

    def approve_alias(self, alias_id, by=None):
        return self._call("approve_alias", alias_id, by=by)

    def link_entities(self, a, b, by=None):
        return self._call("link_entities", a, b, by=by)

    def entities(self, viewer=None):
        return self._call("entities", viewer=viewer)

    # -- B6: imported sources, passages, exact quotations ---------------------------------
    def import_file(self, path, *, author_kind, author_name="", publisher="", reference="",
                    captured_at="", title="", private=False, fmt=None, keep_query=False):
        """Import a local .txt / .md / .html file into YOUR file (see brain/sources.py)."""
        from . import sources as B6
        return B6.import_file(self.store, path, author_kind=author_kind, author_name=author_name,
                              publisher=publisher, reference=reference, captured_at=captured_at,
                              title=title, private=private, fmt=fmt, keep_query=keep_query)

    def reimport(self, source_id, path=None):
        from . import sources as B6
        return B6.reimport(self.store, source_id, path)

    def cite(self, claim_id, source_id, quote, kind="direct"):
        from . import sources as B6
        return B6.cite(self.store, claim_id, source_id, quote, kind)

    def attest_source(self, source_id, note):
        """You attest the AUTHORSHIP of one of your sources (not its facts)."""
        from . import sources as B6
        return B6.attest(self.store, source_id, self._me_id, note)

    def versions(self, source_id):
        from . import sources as B6
        B6._own_source(self.store, source_id)
        return B6.versions(self.store, source_id)

    def purge_version(self, source_id, version):
        """Remove one version's text for good (your own sources). Also says which
        SK-managed backups may still hold earlier copies (release_backups)."""
        from . import sources as B6
        from .upgrade import backups_holding
        v = self.store._db.execute("SELECT text FROM main.source_versions WHERE source_id=? "
                                   "AND version=?", (source_id, version)).fetchone()
        needles = _needles(v[0] if v else None)
        out = B6.purge_version(self.store, source_id, version)
        out["backups"] = backups_holding(os.path.dirname(self._lib.root), needles)
        return out

    def suggestions(self, limit=100):
        """At most `limit` open suggestions; nothing is learnt until you accept one."""
        from . import sources as B6
        return B6.suggestions(self.store, limit)

    def accept_suggestion(self, candidate_id):
        from . import sources as B6
        return B6.accept(self.store, candidate_id)

    def dismiss_suggestion(self, candidate_id):
        from . import sources as B6
        return B6.dismiss(self.store, candidate_id)

    def search_passages(self, q, limit=20, viewer=None):
        from . import sources as B6
        self._me(viewer)
        return B6.search_passages(self.store, q, limit)

    # -- proposals: only what the owner selects goes to their outbox -------------------
    def _grant(self, source_id: int, levels) -> dict:
        db = self.store._db
        src = db.execute("SELECT * FROM main.sources WHERE id=?", (source_id,)).fetchone()
        if src is None:
            raise BrainError("not one of your sources")
        if src["private"]:
            raise BrainError("a private source is never shared")
        levels = sorted(set(levels or ()))
        if set(levels) - set(LEVELS):
            raise BrainError(f"permission levels are {', '.join(LEVELS)}")
        g = db.execute("SELECT * FROM main.source_grants WHERE source_id=?",
                       (source_id,)).fetchone()
        if g is not None and g["withdrawn_at"]:
            raise BrainError("permission for this source was withdrawn; add it again as a new "
                             "source if you want to share it")
        with self.store._lock:
            if g is None:
                db.execute("INSERT INTO main.source_grants (source_id, opaque_id, token, levels, "
                           "granted_at) VALUES (?,?,?,?,?)",
                           (source_id, uuid.uuid4().hex, secrets.token_hex(32),
                            json.dumps(levels), _now()))
            else:
                levels = sorted(set(json.loads(g["levels"])) | set(levels))
                db.execute("UPDATE main.source_grants SET levels=?, granted_at=? WHERE "
                           "source_id=?", (json.dumps(levels), _now(), source_id))
            db.commit()
        g = dict(db.execute("SELECT * FROM main.source_grants WHERE source_id=?",
                            (source_id,)).fetchone())
        return {"src": dict(src), "grant": g, "levels": json.loads(g["levels"])}

    def propose(self, claim_id: int, permissions: Optional[Dict[int, list]] = None) -> str:
        """Put ONE of your claims in your outbox for moderators, with exactly
        the source material you allow: permissions = {source_id: levels}, levels
        from quote / details / full_text (none = an anonymous stub only).
        Returns the proposal token. Nothing else of yours is copied."""
        db = self.store._db
        c = db.execute("SELECT * FROM main.claims WHERE id=?", (claim_id,)).fetchone()
        if c is None or c["owner_id"] != self._me_id or c["scope"] != "personal":
            raise BrainError("only your own personal claims can be proposed")
        permissions = permissions or {}
        rows = db.execute("SELECT e.*, s.private FROM main.evidence e JOIN main.sources s ON "
                          "s.id = e.source_id WHERE e.claim_id=? ORDER BY e.id",
                          (claim_id,)).fetchall()
        token = secrets.token_hex(16)
        out = []
        versions = []                            # B6: the proposed source versions
        checks = []                              # B6: the owner's strict check of each quote
        from . import sources as B6
        for e in rows:
            if e["source_id"] in permissions and e["private"]:
                raise BrainError("a private source is never shared")
            if e["private"] or e["source_id"] not in permissions:
                continue                        # only sources the owner names are included
            g = self._grant(e["source_id"], permissions[e["source_id"]])
            src, lv = g["src"], g["levels"]
            plain = src["content_hash"] or g["grant"]["opaque_id"]
            full_text = src["raw_text"]
            if B6.is_imported(self.store, e["source_id"]):
                v = B6.current_version(self.store, e["source_id"])
                if v is None or v["author_kind"] in B6.NOT_EVIDENCE:
                    raise BrainError("self-written, AI-generated or purged material is not "
                                     "proposed as evidence")
                if "quote" in lv and e["kind"] in ("direct", "confirmation"):
                    # the owner's COMPLETE passage must pass the strict check for this
                    # claim's wording: a shared copy without the text can never
                    # check it again, so nothing weaker may leave this file
                    ok, why = B6.evidence_check(self.store, e["id"], dict(c), strict=True)
                    if not ok:
                        raise BrainError("this quotation does not state the claim by itself in "
                                         f"its passage ({why}); it is not proposed as "
                                         "evidence - cite a passage that states it exactly")
                    pas = self.store._db.execute(
                        "SELECT p.kind, p.flags FROM main.evidence_locations l JOIN "
                        "main.passages p ON p.id=l.passage_id WHERE l.evidence_id=?",
                        (e["id"],)).fetchone()
                    from .store import _text_sha
                    checks.append((token, g["grant"]["opaque_id"],
                                   hashlib.sha256((e["locator"] or "").encode()).hexdigest(),
                                   pas["kind"], pas["flags"],
                                   _text_sha(c["text"], c["subject"] or "")))
                full_text = v["text"]
                versions.append((token, g["grant"]["opaque_id"], v["fmt"],
                                 v["structure"] if "full_text" in lv else "[]",
                                 self._lib.tag(v["text_tag"]), v["author_kind"], "declared",
                                 v["author_name"] if "details" in lv else None,
                                 v["publisher"] if "details" in lv else None,
                                 v["reference"] if "details" in lv else None,
                                 v["flags"]))
            out.append((token, e["kind"], g["grant"]["opaque_id"], src["kind"],
                        self._lib.tag(plain), self._lib.token_hash(g["grant"]["token"]),
                        e["locator"] if "quote" in lv else None,
                        src["title"] if "details" in lv else None,
                        src["reference"] if "details" in lv else None,
                        src["captured_at"] if "details" in lv else None,
                        full_text if "full_text" in lv else None, json.dumps(lv)))
        for row in out:                          # before anything reaches the outbox
            self._lib.register_token(row[2], row[5])
        con = self._lib._outbox(self._user["file_id"], write=True)
        try:
            con.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?)",
                        (token, claim_id, c["text"], c["subject"], c["category"], c["origin"],
                         _now()))
            con.executemany("INSERT INTO material (token, kind, opaque_id, source_kind, tag, "
                            "token_hash, quote, title, reference, captured_at, full_text, levels) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", out)
            con.executemany("INSERT OR REPLACE INTO material_versions VALUES "
                            "(?,?,?,?,?,?,?,?,?,?,?)", versions)
            con.executemany("INSERT OR REPLACE INTO material_checks VALUES (?,?,?,?,?,?)",
                            checks)
            con.commit()
        finally:
            con.close()
        return token

    def withdraw_proposal(self, token: str) -> None:
        """Take a proposal back (before a decision): removed from the outbox."""
        con = self._lib._outbox(self._user["file_id"], write=True)
        try:
            con.execute("DELETE FROM material WHERE token=?", (token,))
            con.execute("DELETE FROM material_versions WHERE token=?", (token,))
            con.execute("DELETE FROM material_checks WHERE token=?", (token,))
            con.execute("DELETE FROM proposals WHERE token=?", (token,))
            con.commit()
            checkpoint(con)
        finally:
            con.close()

    def decisions(self) -> Dict[str, dict]:
        """Moderators' decisions on your proposals (read from shared.db by token)."""
        con = self._lib._outbox(self._user["file_id"], write=False)
        try:
            tokens = [r[0] for r in con.execute("SELECT token FROM proposals")]
        finally:
            con.close()
        out = {}
        for t in tokens:
            r = self.store._db.execute("SELECT decision, note, claim_id FROM "
                                       "shared.proposal_decisions WHERE token=?", (t,)).fetchone()
            if r:
                out[t] = dict(r)
        return out

    # -- withdrawing permission ----------------------------------------------------------
    def withdraw_source(self, source_id: int) -> str:
        """Withdraw everything shared of one of your sources: blocked at once
        everywhere, then physically removed. Returns 'complete' or
        'purge_pending' (finished automatically later). The source stays
        complete in your own file."""
        db = self.store._db
        g = db.execute("SELECT * FROM main.source_grants WHERE source_id=?",
                       (source_id,)).fetchone()
        if g is None:
            return "nothing_shared"
        self._lib.block(g["opaque_id"], g["token"])                       # step 1 (the intent)
        self._lib._crash("after_block")
        with self.store._lock:                                            # step 2
            db.execute("UPDATE main.source_grants SET levels='[]', withdrawn_at=COALESCE("
                       "withdrawn_at, ?), withdraw_state='blocked' WHERE source_id=?",
                       (_now(), source_id))
            db.commit()
        self._lib._crash("after_personal")
        return self.finish_pending()

    def finish_pending(self) -> str:
        """Finish interrupted withdrawals (also run when a session opens)."""
        db = self.store._db
        state = "complete"
        # step 1 is the durable intent: a block in shared.db for one of MY sources
        # whose own record (step 2) was lost in a crash is completed here
        with self.store._lock:
            db.execute("UPDATE main.source_grants SET levels='[]', withdrawn_at=COALESCE("
                       "withdrawn_at, ?), withdraw_state='blocked' WHERE withdraw_state IS NULL "
                       "AND opaque_id IN (SELECT opaque_id FROM shared.withdrawals)", (_now(),))
            db.commit()
        for g in db.execute("SELECT * FROM main.source_grants WHERE withdraw_state IN "
                            "('blocked', 'outbox_done')").fetchall():
            if g["withdraw_state"] == "blocked":                          # step 3
                con = self._lib._outbox(self._user["file_id"], write=True)
                try:
                    con.execute("UPDATE material SET quote=NULL, title=NULL, reference=NULL, "
                                "captured_at=NULL, full_text=NULL, tag=NULL, levels='[]' "
                                "WHERE opaque_id=?", (g["opaque_id"],))
                    con.execute("DELETE FROM material_versions WHERE opaque_id=?",
                                (g["opaque_id"],))
                    con.execute("DELETE FROM material_checks WHERE opaque_id=?",
                                (g["opaque_id"],))
                    con.commit()
                    self._lib._crash("outbox_after_delete")
                    checkpoint(con)
                finally:
                    con.close()
                with self.store._lock:
                    db.execute("UPDATE main.source_grants SET withdraw_state='outbox_done' "
                               "WHERE source_id=?", (g["source_id"],))
                    db.commit()
                self._lib._crash("after_outbox")
        pending = self._lib.purge_shared()                               # steps 4-6
        for g in db.execute("SELECT * FROM main.source_grants WHERE withdraw_state="
                            "'outbox_done'").fetchall():
            done = db.execute("SELECT state FROM shared.withdrawals WHERE opaque_id=?",
                              (g["opaque_id"],)).fetchone()
            if done and done[0] == "complete":
                with self.store._lock:
                    db.execute("UPDATE main.source_grants SET withdraw_state='complete' "
                               "WHERE source_id=?", (g["source_id"],))
                    db.commit()
            else:
                state = "purge_pending"
        return "purge_pending" if pending else state

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.store.close()


class ModeratorSession(_Base):
    """A moderator: reviews proposals (outboxes, READ-ONLY) and writes the
    shared file through review methods. A moderator's own knowledge is in
    their own personal file (a normal Session)."""

    def __init__(self, lib: Library, user: dict):
        super().__init__(lib, user)
        self._me_id = user["pseudonym"]
        self.store = KnowledgeStore(lib.path("shared.db"), role="shared",
                                    allocator=lib.allocator())
        with self.store._lock:
            self.store._db.execute("INSERT OR IGNORE INTO main.contributors VALUES (?,?,?,?)",
                                   (self._me_id, user["display_name"], "moderator", _now()))
            self.store._db.commit()
        from .api import Brain
        self.brain = Brain._over(self.store)

    def _blocked(self) -> set:
        return {r[0] for r in self.store._db.execute("SELECT opaque_id FROM withdrawals")}

    def proposals(self) -> List[dict]:
        """Open proposals of every user, read-only - without anything withdrawn."""
        decided = {r[0] for r in self.store._db.execute("SELECT token FROM proposal_decisions")}
        blocked = self._blocked()
        out = []
        for u in self._lib.users():
            if not os.path.exists(self._lib.path("outbox", u["file_id"] + ".db")):
                continue
            con = self._lib._outbox(u["file_id"], write=False)
            try:
                con.row_factory = sqlite3.Row
                for p in con.execute("SELECT token, text, subject, category, origin FROM "
                                     "proposals ORDER BY created, token").fetchall():
                    if p["token"] in decided:
                        continue
                    mat = [dict(m) for m in con.execute(
                        "SELECT kind, opaque_id, source_kind, quote, title, reference, "
                        "captured_at, full_text, levels FROM material WHERE token=? ORDER BY id",
                        (p["token"],)) if m["opaque_id"] not in blocked]
                    out.append({"file": u["file_id"], "token": p["token"], "text": p["text"],
                                "subject": p["subject"], "category": p["category"],
                                "material": mat})
            finally:
                con.close()
        return out

    def approve(self, file_id: str, token: str, note: str = "") -> int:
        """Copy a proposal into shared.db as a NEW claim that starts UNVERIFIED
        (the owner's own verification never carries over). The block check
        happens inside the same shared-file transaction, so a withdrawal is
        either seen here or removes the copy afterwards. Returns the claim id."""
        st = self.store
        db = st._db
        con = self._lib._outbox(file_id, write=False)
        try:
            con.row_factory = sqlite3.Row
            p = con.execute("SELECT * FROM proposals WHERE token=?", (token,)).fetchone()
            mat = [dict(m) for m in con.execute("SELECT * FROM material WHERE token=? ORDER BY id",
                                                (token,))]
            mver = {m["opaque_id"]: dict(m) for m in con.execute(
                "SELECT * FROM material_versions WHERE token=?", (token,))}
            mchk = {(m["opaque_id"], m["quote_sha"]): dict(m) for m in con.execute(
                "SELECT * FROM material_checks WHERE token=?", (token,))}
            pseud = con.execute("SELECT value FROM meta WHERE key='pseudonym'").fetchone()[0]
        finally:
            con.close()
        if p is None:
            raise BrainError("no such proposal")
        with st._lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                done = db.execute("SELECT claim_id FROM proposal_decisions WHERE token=?",
                                  (token,)).fetchone()
                if done:
                    db.rollback()
                    return done[0]
                blocked = {r[0] for r in db.execute("SELECT opaque_id FROM withdrawals")}
                db.execute("INSERT OR IGNORE INTO main.contributors VALUES (?,?,?,?)",
                           (pseud, "contributor", "external_user", _now()))
                now = _now()
                cid = st._new("claims", "INSERT INTO main.claims (text, subject, category, origin, "
                              "status, scope, owner_id, section_id, version, created, created_by, "
                              "updated) VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                              (p["text"], p["subject"], p["category"], p["origin"],
                               "awaiting_verification", "shared", pseud, None, now, pseud,
                               now)).lastrowid
                st._index_claim(cid, p["text"], p["subject"], p["category"])
                st._event(cid, "share_approved", None, "awaiting_verification", self._me_id,
                          "approved from a proposal" + (f": {note}" if note else ""))
                for m in mat:
                    if m["opaque_id"] in blocked:
                        continue                   # withdrawn: never copied
                    r = db.execute("SELECT source_id FROM source_opaque WHERE opaque_id=?",
                                   (m["opaque_id"],)).fetchone()
                    if r:
                        sid = r[0]
                    else:
                        mv = mver.get(m["opaque_id"])
                        sid = st._new("sources", "INSERT INTO main.sources (kind, title, "
                                      "reference, content_hash, captured_at, added_at, added_by, "
                                      "private, raw_text, meta) VALUES (?,?,?,?,?,?,?,0,?,'{}')",
                                      (m["source_kind"], m["title"], m["reference"], m["tag"],
                                       m["captured_at"], now, pseud,
                                       None if mv else m["full_text"])).lastrowid
                        db.execute("INSERT INTO main.source_opaque VALUES (?,?)",
                                   (sid, m["opaque_id"]))
                        db.execute("INSERT OR IGNORE INTO main.source_tokens VALUES (?,?)",
                                   (m["opaque_id"], m["token_hash"]))
                        if mv:
                            self._shared_version(sid, mv, m, pseud, now)
                    eid = st._new("evidence", "INSERT INTO main.evidence (claim_id, source_id, "
                                  "kind, locator, added_by, added_at) VALUES (?,?,?,?,?,?)",
                                  (cid, sid, m["kind"], m["quote"] or "", pseud, now)).lastrowid
                    self._shared_location(eid, sid, m["quote"] or "")
                    chk = mchk.get((m["opaque_id"], hashlib.sha256(
                        (m["quote"] or "").encode()).hexdigest()))
                    if chk is not None and m["quote"]:
                        db.execute("INSERT INTO main.quote_checks VALUES (?,?,?,?,?)",
                                   (eid, chk["passage_kind"], chk["passage_flags"],
                                    chk["claim_sha"], now))
                db.execute("INSERT INTO main.proposal_decisions VALUES (?,?,?,?,?,?)",
                           (token, "approved", note, cid, self._me_id, now))
                db.commit()
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
        return cid

    def _shared_version(self, sid: int, mv: dict, m: dict, pseud: str, now: str) -> None:
        """B6: the shared copy of a proposed source version. Its text and passages
        only with a full_text grant (positions are rebuilt here from the text and
        its structure); otherwise the declared origin alone."""
        from . import sources as B6
        st = self.store
        text = m["full_text"]
        structure = mv["structure"] if text is not None else "[]"
        vid = st._new("source_versions", "INSERT INTO main.source_versions (source_id, version, "
                      "fmt, text, text_tag, bytes_tag, size, structure, author_kind, author_name, "
                      "publisher, reference, captured_at, provenance, imported_by, imported_at, "
                      "flags, state) VALUES (?,1,?,?,?,NULL,?,?,?,?,?,?,?,'declared',?,?,?,"
                      "'current')",
                      (sid, mv["fmt"], text, mv["text_tag"], len(text) if text else None,
                       structure, mv["author_kind"], mv["author_name"], mv["publisher"],
                       mv["reference"], m["captured_at"], pseud, now,
                       mv.get("flags") or "[]")).lastrowid
        if text is not None:                     # source warnings carried to every passage
            B6.store_passages(st, vid, sid, B6._Text(text, structure,
                                                     json.loads(mv.get("flags") or "[]")),
                              sha=self._lib.tag)

    def _shared_location(self, eid: int, sid: int, quote: str) -> None:
        """B6: where a shared quotation is in the shared copy's text (if it has text)."""
        from . import sources as B6
        import hashlib
        v = B6.current_version(self.store, sid)
        if v is None or v["text"] is None or not quote:
            return
        loc = B6.locate(self.store, v, quote)
        if loc is not None:
            self.store._db.execute("INSERT INTO main.evidence_locations VALUES (?,?,?,?,?,?)",
                                   (eid, v["id"], loc["passage"]["id"], loc["start"], loc["end"],
                                    self._lib.tag(hashlib.sha256(quote.encode()).hexdigest())))

    def attest_source(self, source_id: int, note: str) -> dict:
        """A moderator attests the AUTHORSHIP of a shared source (not its facts).
        Needed before shared knowledge resting only on imported sources can be
        verified; an owner's own attestation never carries over."""
        from . import sources as B6
        return B6.attest(self.store, source_id, self._me_id, note)

    def search_passages(self, q, limit=20):
        from . import sources as B6
        return B6.search_passages(self.store, q, limit)

    def reject(self, file_id: str, token: str, note: str = "") -> None:
        with self.store._lock:
            self.store._db.execute("INSERT OR IGNORE INTO main.proposal_decisions VALUES "
                                   "(?,?,?,NULL,?,?)", (token, "rejected", note, self._me_id,
                                                        _now()))
            self.store._db.commit()

    def review(self, claim_id: int, status: str, note: str = "") -> None:
        """Only a moderator's review in the shared file verifies shared knowledge."""
        self.brain.review(claim_id, status, self._me_id, note)

    def review_list(self) -> List[dict]:
        """Shared claims flagged: verified, but evidence was withdrawn."""
        db = self.store._db
        return [dict(r) for r in db.execute(
            "SELECT DISTINCT c.id, c.text, c.status FROM claims c JOIN evidence e ON "
            "e.claim_id=c.id WHERE e.id IN (SELECT evidence_id FROM evidence_withdrawn) OR "
            "e.source_id IN (SELECT o.source_id FROM source_opaque o JOIN withdrawals w ON "
            "w.opaque_id=o.opaque_id) ORDER BY c.id")]

    def add_relation(self, key, value_type, sentence_forms, question_forms, labels=None, **kw):
        return self.brain.add_relation(key, value_type, sentence_forms, question_forms, labels,
                                       by=self._me_id, **kw)

    def add_entity(self, name, kind=""):
        return self.brain.add_entity(name, kind, "shared", by=self._me_id)

    def add_alias(self, entity_id, alias, language=""):
        return self.brain.add_alias(entity_id, alias, language, by=self._me_id)

    def approve_alias(self, alias_id):
        return self.brain.approve_alias(alias_id, by=self._me_id)

    def link_entities(self, a, b):
        return self.brain.link_entities(a, b, by=self._me_id)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.store.close()
