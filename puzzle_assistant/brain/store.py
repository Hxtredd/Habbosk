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
import json
import os
import re
import sqlite3
import threading
import uuid
from typing import Dict, List, Optional

from .model import (AUTOMATIC_KINDS, EVIDENCE_KINDS, MODERATOR_KINDS, ORIGINS, SCOPES,
                    SOURCE_KINDS, STATUSES)
from .schema import MIGRATIONS, SCHEMA, SCHEMA_VERSION
from .text import INDEX_VERSION, index_text, normalize, polarity

SYSTEM = "system"
IMPORTER = "importer"


class BrainError(ValueError):
    """A request that would break the Brain's rules (nothing was changed)."""


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


class KnowledgeStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._depth = 0
        self._db = sqlite3.connect(path, check_same_thread=False)
        try:
            self._open()
        except BaseException:
            self._db.close()                 # never leave the file open (Windows keeps it locked)
            raise

    def _open(self) -> None:
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._migrate()
        self.fts = self._setup_fts()
        for cid, name, kind in ((SYSTEM, "SK Assistant", "system"),
                                (IMPORTER, "Importer", "importer")):
            self._db.execute("INSERT OR IGNORE INTO contributors VALUES (?,?,?,?)",
                             (cid, name, kind, _now()))
        if self._meta("local_user") is None:
            uid = str(uuid.uuid4())
            self._db.execute("INSERT INTO contributors VALUES (?,?,?,?)",
                             (uid, "Local user", "local_user", _now()))
            self._set_meta("local_user", uid)
        self._db.commit()
        self._ensure_index()

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
        t = index_text(text, subject or "", category or "")
        self._db.execute("INSERT OR REPLACE INTO claim_index (claim_id, terms, subject_terms, norm, "
                         "subject_norm, positive) VALUES (?,?,?,?,?,?)",
                         (claim_id, t, index_text(subject or ""),
                          normalize(f"{subject or ''} {text}"), normalize(subject or ""),
                          int(polarity(text))))
        if self.fts:
            try:
                self._db.execute("DELETE FROM claim_fts WHERE rowid=?", (claim_id,))
                self._db.execute("INSERT INTO claim_fts (rowid, terms) VALUES (?,?)", (claim_id, t))
            except sqlite3.Error:
                self._fts_off()
        else:                                # written without FTS5: that table is now behind
            self._set_meta("fts_stale", "1")

    def _ensure_index(self) -> None:
        """Rebuild the index if it is missing, incomplete or from an older
        version of the word rules. Only index tables are written."""
        with self._lock:
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(claim_index)")}
            if "positive" not in cols:       # index from an earlier development build:
                self._db.execute("DROP TABLE claim_index")      # derived data, rebuilt below
                self._db.executescript(SCHEMA)
            n_claims = self._db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            n_index = self._db.execute("SELECT COUNT(*) FROM claim_index").fetchone()[0]
            n_fts = n_claims
            if self.fts:
                try:
                    n_fts = self._db.execute("SELECT COUNT(*) FROM claim_fts").fetchone()[0]
                except sqlite3.Error:
                    self.fts = False
            fts_stale = self.fts and self._meta("fts_stale") == "1"
            if (self._meta("index_version") == str(INDEX_VERSION) and n_index == n_claims
                    and n_fts == n_claims and not fts_stale):
                return
            self._db.execute("DELETE FROM claim_index")
            if self.fts:
                try:
                    self._db.execute("DELETE FROM claim_fts")
                except sqlite3.Error:
                    self.fts = False
            for r in self._db.execute("SELECT id, text, subject, category FROM claims").fetchall():
                self._index_claim(r[0], r[1], r[2], r[3])
            self._set_meta("index_version", str(INDEX_VERSION))
            if self.fts:
                self._db.execute("DELETE FROM meta WHERE key='fts_stale'")
            self._db.commit()

    def _visible_sql(self, viewer: Optional[str], alias: str = "c"):
        """WHERE-clause part that keeps only claims `viewer` may see."""
        viewer = viewer or self.local_user
        r = self._db.execute("SELECT kind FROM contributors WHERE id=?", (viewer,)).fetchone()
        mod = bool(r) and r[0] in MODERATOR_KINDS
        clause = (f"({alias}.scope='shared' OR {alias}.owner_id=?"
                  + (f" OR {alias}.scope='shared_candidate'" if mod else "") + ")")
        return clause, [viewer]

    def search_rows(self, viewer: Optional[str], match_terms: Optional[List[str]] = None,
                    use_fts: Optional[bool] = None) -> List[dict]:
        """Index rows of the claims `viewer` may see. With match_terms, only
        claims containing at least one of them (FTS5 when available, else
        the same test in Python - identical eligibility)."""
        use_fts = self.fts if use_fts is None else (use_fts and self.fts)
        with self._lock:
            vis, args = self._visible_sql(viewer)
            q = ("SELECT c.id, c.text, c.subject, c.category, c.status, c.origin, c.scope, "
                 "c.owner_id, c.version, i.terms, i.subject_terms, i.norm, i.subject_norm, "
                 "i.positive "
                 "FROM claims c "
                 f"JOIN claim_index i ON i.claim_id = c.id WHERE {vis}")
            rows = None
            if match_terms and use_fts:
                try:
                    rows = [dict(r) for r in self._db.execute(
                        q + " AND c.id IN (SELECT rowid FROM claim_fts WHERE claim_fts MATCH ?)",
                        args + [" OR ".join('"%s"' % t.replace('"', '') for t in match_terms)])]
                except sqlite3.Error:        # FTS5 broke while running: same search in Python
                    self._fts_off()
                    self._db.commit()
                    use_fts = False
            if rows is None:
                rows = [dict(r) for r in self._db.execute(q, args)]
        if match_terms and not use_fts:
            want = set(match_terms)
            rows = [r for r in rows if want & set(r["terms"].split())]
        return rows

    def visible_subjects(self, viewer: Optional[str]) -> List[tuple]:
        """(subject, normalised subject) of the viewer's visible claims."""
        with self._lock:
            vis, args = self._visible_sql(viewer)
            return [tuple(r) for r in self._db.execute(
                f"SELECT DISTINCT c.subject, i.subject_norm FROM claims c JOIN claim_index i "
                f"ON i.claim_id = c.id WHERE {vis} AND c.subject != ''", args)]

    def change_stamp(self) -> tuple:
        """Changes whenever a claim is added, corrected, reviewed or relinked."""
        with self._lock:
            return tuple(self._db.execute(
                "SELECT (SELECT COUNT(*) FROM claims), (SELECT MAX(updated) FROM claims), "
                "(SELECT COUNT(*) FROM claim_events), (SELECT COUNT(*) FROM contributors)"
            ).fetchone())

    def visible_count(self, viewer: Optional[str]) -> int:
        with self._lock:
            vis, args = self._visible_sql(viewer)
            return self._db.execute(f"SELECT COUNT(*) FROM claims c WHERE {vis}", args).fetchone()[0]

    def visible_term_counts(self, viewer: Optional[str], words: List[str],
                            use_fts: Optional[bool] = None) -> Dict[str, int]:
        """In how many of the viewer's visible claims each word occurs
        (word rarity is never computed from claims they can't see)."""
        use_fts = self.fts if use_fts is None else (use_fts and self.fts)
        out: Dict[str, int] = {}
        with self._lock:
            vis, args = self._visible_sql(viewer)
            if use_fts:
                for w in words:
                    out[w] = self._db.execute(
                        f"SELECT COUNT(*) FROM claims c WHERE {vis} AND c.id IN "
                        "(SELECT rowid FROM claim_fts WHERE claim_fts MATCH ?)",
                        args + ['"%s"' % w.replace('"', '')]).fetchone()[0]
                return out
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
        r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

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
            self._db.execute("INSERT OR IGNORE INTO contributors VALUES (?,?,?,?)",
                             (cid, display_name, kind, _now()))
        return cid

    def _known_contributor(self, cid: str) -> str:
        """Returns the contributor's kind."""
        r = self._db.execute("SELECT kind FROM contributors WHERE id=?", (cid,)).fetchone()
        if r is None:
            raise BrainError(f"unknown contributor {cid!r}")
        return r[0]

    # -- isolation -------------------------------------------------------------------
    def can_see(self, c: dict, viewer: Optional[str]) -> bool:
        """Personal / candidate claims belong to their owner; shared ones to all.
        Moderators also see candidates (to review them)."""
        if c is None:
            return False
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
        by = by or self.local_user
        with self._lock:
            self._known_contributor(by)
            cur = self._db.execute(
                "INSERT INTO sources (kind, title, reference, content_hash, captured_at, added_at, "
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
            cur = self._db.execute(
                "INSERT INTO sections (source_id, heading_path, position, text) VALUES (?,?,?,?)",
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
        with self._lock:
            self._known_contributor(by)
            if section_id is not None and not self._usable_section(section_id, by):
                raise BrainError(f"no section {section_id}")
            cur = self._db.execute(
                "INSERT INTO claims (text, subject, category, origin, status, scope, owner_id, "
                "section_id, version, created, created_by, updated) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                (text.strip(), subject, category, origin, status, scope, by, section_id, now, by,
                 now))
            cid = cur.lastrowid
            self._index_claim(cid, text.strip(), subject, category)
            self._event(cid, "created", None, status, by, f"origin={origin}")
            return cid

    def _claim(self, claim_id: int) -> Optional[dict]:
        """Internal: no visibility check (use claim() for anything a person sees)."""
        r = self._db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        return dict(r) if r else None

    def claim(self, claim_id: int, viewer: Optional[str] = None) -> Optional[dict]:
        """The claim if `viewer` (default: the local user) may see it, else None."""
        with self._lock:
            c = self._claim(claim_id)
            return c if self.can_see(c, viewer) else None

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
        self._db.execute("INSERT INTO claim_events (claim_id, event, old_status, new_status, by, "
                         "at, note, related_claim) VALUES (?,?,?,?,?,?,?,?)",
                         (claim_id, event, old, new, by, _now(), note, related))

    def history(self, claim_id: int, viewer: Optional[str] = None) -> Dict[str, list]:
        with self._lock:
            if not self.can_see(self._claim(claim_id), viewer):
                return {"events": [], "versions": []}
            ev = []
            for r in self._db.execute("SELECT * FROM claim_events WHERE claim_id=? ORDER BY id",
                                      (claim_id,)).fetchall():
                e = dict(r)
                # events about another claim are shown only if that claim is
                # visible too (never its id, wording, relation or author)
                rel = e.get("related_claim")
                refs = [rel] if rel is not None else \
                    [int(x) for x in re.findall(r"claim (\d+)", e.get("note") or "")]
                if all(self.can_see(self._claim(x), viewer) for x in refs):
                    ev.append(e)
            ver = [dict(r) for r in self._db.execute(
                "SELECT * FROM claim_versions WHERE claim_id=? ORDER BY version", (claim_id,))]
        return {"events": ev, "versions": ver}

    # -- evidence ------------------------------------------------------------------
    @_atomic
    def add_evidence(self, claim_id: int, kind: str, source_id: Optional[int] = None,
                     locator: str = "", by: Optional[str] = None) -> int:
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
            if source_id is not None:
                src = self._usable_source(source_id, by)
                if src is None:
                    raise BrainError(f"no source {source_id} (evidence must be traceable)")
                if src["private"] and c["scope"] != "personal":
                    raise BrainError("a shared / proposed claim cannot get evidence from a "
                                     "private source")
            if kind == "derived" and not self._db.execute(
                    "SELECT 1 FROM claim_links WHERE from_claim=? AND kind='derived_from'",
                    (claim_id,)).fetchone():
                raise BrainError("derived evidence needs a 'derived_from' link to the claims "
                                 "it comes from")
            cur = self._db.execute(
                "INSERT INTO evidence (claim_id, source_id, kind, locator, added_by, added_at) "
                "VALUES (?,?,?,?,?,?)", (claim_id, source_id, kind, locator, by, _now()))
            self._event(claim_id, "evidence_added", None, None, by,
                        f"{kind}" + (f" from source {source_id}" if source_id else ""))
            return cur.lastrowid

    def evidence(self, claim_id: int) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT e.*, s.kind AS source_kind, s.content_hash, s.private, s.title, "
                "s.reference FROM evidence e LEFT JOIN sources s ON s.id = e.source_id "
                "WHERE e.claim_id=? ORDER BY e.id", (claim_id,))]

    def evidence_summary(self, claim_id: int) -> dict:
        """How well supported a claim is. The same material (same content
        hash) counts once, however many people submit it; assertions are
        counted separately and never count as support."""
        ev = self.evidence(claim_id)
        direct, confirm = set(), set()
        for e in ev:
            key = e["content_hash"] or f"source:{e['source_id']}"
            if e["kind"] == "direct":
                direct.add(key)
            elif e["kind"] == "confirmation":
                confirm.add(key)
        confirm -= direct
        return {"direct": len(direct), "confirmations": len(confirm),
                "derived": sum(1 for e in ev if e["kind"] == "derived"),
                "assertions": sum(1 for e in ev if e["kind"] == "assertion"),
                "contributors": len({e["added_by"] for e in ev}),
                "private_sources": sum(1 for e in ev if e["private"])}

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
            self._db.execute("UPDATE claims SET status=?, updated=? WHERE id=?",
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
            self._db.execute(
                "INSERT INTO claim_versions (claim_id, version, text, subject, category, "
                "old_status, replaced_by, replaced_at, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                (claim_id, c["version"], c["text"], c["subject"], c["category"], c["status"],
                 by, _now(), reason))
            v = c["version"] + 1
            self._db.execute(
                "UPDATE claims SET text=?, subject=?, category=?, version=?, status=?, updated=? "
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
            self._db.execute("INSERT INTO claim_links (from_claim, to_claim, kind, added_by, "
                             "added_at) VALUES (?,?,'supersedes',?,?)", (new, claim_id, by, _now()))
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
            self._db.execute("INSERT OR IGNORE INTO claim_links (from_claim, to_claim, kind, "
                             "added_by, added_at) VALUES (?,?,?,?,?)",
                             (from_claim, to_claim, kind, by, _now()))
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
                    self._db.execute("UPDATE claims SET status='conflicting', updated=? "
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
            self._db.execute("UPDATE claims SET scope='shared_candidate', updated=? WHERE id=?",
                             (_now(), claim_id))
            self._event(claim_id, "share_proposed", c["status"], c["status"], by,
                        f"scope {c['scope']} -> shared_candidate")

    @_atomic
    def approve_share(self, claim_id: int, moderator: str, note: str = "") -> None:
        """Shared-candidate -> shared. Only a moderator, only for verified
        claims without private evidence (the shared library prefers accuracy)."""
        with self._lock:
            if self._known_contributor(moderator) not in MODERATOR_KINDS:
                raise BrainError("only a moderator can approve shared knowledge")
            c = self._claim(claim_id)
            if c is None or c["scope"] != "shared_candidate":
                raise BrainError("not a proposed claim")
            if c["status"] != "verified":
                raise BrainError("only verified knowledge enters the shared library")
            if self.evidence_summary(claim_id)["private_sources"]:
                raise BrainError("has evidence from a private source")
            self._db.execute("UPDATE claims SET scope='shared', updated=? WHERE id=?",
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

