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
import json
import os
import sqlite3
import threading
import uuid
from typing import Dict, List, Optional

from .model import (AUTOMATIC_KINDS, EVIDENCE_KINDS, MODERATOR_KINDS, ORIGINS, SCOPES,
                    SOURCE_KINDS, STATUSES)
from .schema import MIGRATIONS, SCHEMA, SCHEMA_VERSION

SYSTEM = "system"
IMPORTER = "importer"


class BrainError(ValueError):
    """A request that would break the Brain's rules (nothing was changed)."""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class KnowledgeStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._migrate()
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

    def close(self) -> None:
        with self._lock:
            self._db.close()

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

    def add_contributor(self, display_name: str, kind: str = "external_user",
                        contributor_id: Optional[str] = None) -> str:
        cid = contributor_id or str(uuid.uuid4())
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO contributors VALUES (?,?,?,?)",
                             (cid, display_name, kind, _now()))
            self._db.commit()
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

    # -- sources and sections ----------------------------------------------------
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
            self._db.commit()
            return cur.lastrowid

    def add_section(self, source_id: int, text: str, heading_path: str = "",
                    position: int = 0) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO sections (source_id, heading_path, position, text) VALUES (?,?,?,?)",
                (source_id, heading_path, position, text))
            self._db.commit()
            return cur.lastrowid

    def source(self, source_id: int) -> Optional[dict]:
        with self._lock:
            r = self._db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(r) if r else None

    # -- claims ------------------------------------------------------------------
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
            cur = self._db.execute(
                "INSERT INTO claims (text, subject, category, origin, status, scope, owner_id, "
                "section_id, version, created, created_by, updated) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                (text.strip(), subject, category, origin, status, scope, by, section_id, now, by,
                 now))
            cid = cur.lastrowid
            self._event(cid, "created", None, status, by, f"origin={origin}")
            self._db.commit()
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
        viewer = viewer or self.local_user
        with self._lock:
            mod = self._db.execute("SELECT kind FROM contributors WHERE id=?",
                                   (viewer,)).fetchone()
        mod = bool(mod) and mod[0] in MODERATOR_KINDS
        q = ("SELECT * FROM claims WHERE (scope='shared' OR owner_id=?"
             + (" OR scope='shared_candidate'" if mod else "") + ")")
        args: list = [viewer]
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
               by: str, note: str = "") -> None:
        self._db.execute("INSERT INTO claim_events (claim_id, event, old_status, new_status, by, "
                         "at, note) VALUES (?,?,?,?,?,?,?)",
                         (claim_id, event, old, new, by, _now(), note))

    def history(self, claim_id: int, viewer: Optional[str] = None) -> Dict[str, list]:
        with self._lock:
            if not self.can_see(self._claim(claim_id), viewer):
                return {"events": [], "versions": []}
            ev = [dict(r) for r in self._db.execute(
                "SELECT * FROM claim_events WHERE claim_id=? ORDER BY id", (claim_id,))]
            ver = [dict(r) for r in self._db.execute(
                "SELECT * FROM claim_versions WHERE claim_id=? ORDER BY version", (claim_id,))]
        return {"events": ev, "versions": ver}

    # -- evidence ------------------------------------------------------------------
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
            self._require_owner(c, by, "add evidence to")
            if source_id is not None:
                src = self.source(source_id)
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
            self._db.commit()
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
                open_conflict = self._db.execute(
                    "SELECT 1 FROM claim_links l JOIN claims o ON o.id = "
                    "CASE WHEN l.from_claim=? THEN l.to_claim ELSE l.from_claim END "
                    "WHERE l.kind='contradicts' AND (l.from_claim=? OR l.to_claim=?) "
                    "AND o.status NOT IN ('rejected','outdated')",
                    (claim_id, claim_id, claim_id)).fetchone()
                if open_conflict:
                    raise BrainError("cannot verify: a contradicting claim is still open "
                                     "(reject or mark it outdated first)")
            self._db.execute("UPDATE claims SET status=?, updated=? WHERE id=?",
                             (new_status, _now(), claim_id))
            self._event(claim_id, "reviewed", c["status"], new_status, reviewer, note)
            self._db.commit()

    # -- corrections: new version, old wording kept --------------------------------
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
            self._event(claim_id, "corrected", c["status"], "awaiting_verification", by,
                        reason or f"version {v}")
            self._db.commit()
            return v

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
            new = self.add_claim(new_text, "user", by, c["subject"], c["category"], c["section_id"])
            self._db.execute("INSERT INTO claim_links (from_claim, to_claim, kind, added_by, "
                             "added_at) VALUES (?,?,'supersedes',?,?)", (new, claim_id, by, _now()))
            self._event(new, "correction_proposed", None, None, by,
                        f"for claim {claim_id}: {reason}")
            self._event(claim_id, "correction_received", None, None, by, f"claim {new}")
            self._db.commit()
            return new

    # -- relations -------------------------------------------------------------------
    def link(self, from_claim: int, to_claim: int, kind: str, by: Optional[str] = None) -> None:
        if kind not in ("supports", "contradicts", "derived_from", "supersedes", "duplicate_of"):
            raise BrainError(f"unknown link kind {kind!r}")
        by = by or self.local_user
        with self._lock:
            self._known_contributor(by)
            a, b = self._claim(from_claim), self._claim(to_claim)
            if not (self.can_see(a, by) and self.can_see(b, by)):
                raise BrainError("both claims must exist and be visible to you")
            self._require_owner(a, by, "link")
            self._db.execute("INSERT OR IGNORE INTO claim_links (from_claim, to_claim, kind, "
                             "added_by, added_at) VALUES (?,?,?,?,?)",
                             (from_claim, to_claim, kind, by, _now()))
            self._event(from_claim, f"link_{kind}", None, None, by, f"-> claim {to_claim}")
            if kind == "contradicts":
                # both sides become 'conflicting' (unless already rejected / outdated)
                for cid in (from_claim, to_claim):
                    c = self._claim(cid)
                    if c and c["status"] not in ("rejected", "outdated", "conflicting"):
                        self._db.execute("UPDATE claims SET status='conflicting', updated=? "
                                         "WHERE id=?", (_now(), cid))
                        self._event(cid, "conflict_flagged", c["status"], "conflicting", by,
                                    f"contradicted by claim {to_claim if cid == from_claim else from_claim}")
            self._db.commit()

    def links(self, claim_id: int, viewer: Optional[str] = None) -> List[dict]:
        with self._lock:
            if not self.can_see(self._claim(claim_id), viewer):
                return []
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM claim_links WHERE from_claim=? OR to_claim=? ORDER BY id",
                (claim_id, claim_id))]

    # -- scope: personal -> shared candidate (explicit, recorded) ----------------
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
            self._db.commit()

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
            self._db.commit()

    def stats(self, viewer: Optional[str] = None) -> dict:
        """Counts of the claims this viewer may see (never other people's)."""
        with self._lock:
            out = {s: 0 for s in STATUSES}
            for c in self.claims(limit=10 ** 9, viewer=viewer):
                out[c["status"]] += 1
            out["sources"] = self._db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            out["claims"] = sum(out[s] for s in STATUSES)
        return out

