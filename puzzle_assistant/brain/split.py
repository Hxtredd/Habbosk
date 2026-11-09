"""B5.2 migration: one older knowledge.db -> the separated library layout.

    report = split_file(src_db, data_dir, host_key, library_key)
    check  = recovery_check(data_dir, host_key)            # read-only, any time

The original is NEVER opened by SQLite and never written: it is only read as
bytes into copies. Its fingerprints (size, modification time, SHA-256 of the
file and its sidecars) are compared at every step; any difference stops.

  1. backup        brain-backups/<id>/  (byte copy, hash checked)
  2. manifests     B = brain-backups/<id>/manifest.json, C = brain-anchor-<id>.json,
                   written FIRST as "pending" with the original's and backup's
                   fingerprints; each carries a checksum and an HMAC (host key)
  3. staging       brain-staging-<id>/  built from a second byte copy: every
                   original row placed in exactly ONE file by fixed rules
                   (shared knowledge -> shared.db, everything else -> its
                   owner's personal file); owners' source material referenced
                   by shared claims is NOT assumed shareable: shared.db gets
                   only a stub (same id) and the quotes are withheld
  4. verification  every original row once, unchanged; nothing personal in
                   shared.db; integrity; the same answers per person
  5. manifest A    brain-staging-<id>/manifest.json, complete
  6. COMMIT        one rename: brain-staging-<id> -> brain
  7. B, C          updated to "complete"
A failure before 6 removes the staging folder; the original and backup are
untouched. Recovery never deletes anything on its own (recovery_check).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import uuid
from typing import Dict, List, Optional, Tuple

from .library import Library, _atomic_write, checkpoint
from .model import MODERATOR_KINDS
from .schema import SCHEMA_VERSION
from .store import BrainError, KnowledgeStore, file_uri, library_pragmas, peek_schema_version

SIDECARS = ("-journal", "-wal", "-shm")
TABLES = ("contributors", "sources", "sections", "claims", "evidence", "claim_links",
          "claim_versions", "claim_events", "relations", "entities", "aliases", "entity_links")
USERS = ("local_user", "external_user", "moderator")
BOOKKEEPING_META = ("schema_version", "local_user", "index_version", "facts_stamp", "fts_stale")


class RecoveryNeeded(BrainError):
    """The library does not match its migration record: nothing was opened
    for answering or writing. Run recovery_check()."""


# -- fingerprints ---------------------------------------------------------------------------
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: str) -> Dict[str, Optional[list]]:
    out = {}
    for s in ("",) + SIDECARS:
        p = path + s
        if os.path.isfile(p):
            st = os.stat(p)
            out[s or "db"] = [st.st_size, st.st_mtime_ns, sha256_file(p)]
        else:
            out[s or "db"] = None
    return out


def _same_content(a: dict, b: dict) -> bool:
    """Same size and SHA-256 for the file and every sidecar (mtime is reported,
    not required: a copy restored by a person may carry another time)."""
    return all((a.get(k) or [None, None, None])[0::2] == (b.get(k) or [None, None, None])[0::2]
               for k in set(a) | set(b))


# -- manifests: checksum (corruption) + HMAC (authenticity) ---------------------------------------
def _canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_manifest(path: str, body: dict, host_key: bytes) -> None:
    doc = {"body": body, "sha256": hashlib.sha256(_canon(body)).hexdigest(),
           "hmac": hmac.new(host_key, _canon(body), hashlib.sha256).hexdigest()}
    _atomic_write(path, json.dumps(doc, indent=1, sort_keys=True).encode())


def read_manifest(path: str, host_key: Optional[bytes]) -> Tuple[str, Optional[dict]]:
    """(trust, body): trust is 'authentic' (checksum AND HMAC valid),
    'unauthenticated' (checksum valid, no key to check the HMAC), 'untrusted'
    (checksum or HMAC wrong, unreadable) or 'missing'."""
    if not os.path.isfile(path):
        return "missing", None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        body = doc["body"]
        ok_sum = hmac.compare_digest(hashlib.sha256(_canon(body)).hexdigest(), doc["sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return "untrusted", None
    if not ok_sum:
        return "untrusted", None
    if not host_key:
        return "unauthenticated", body
    mac = hmac.new(host_key, _canon(body), hashlib.sha256).hexdigest()
    return ("authentic", body) if hmac.compare_digest(mac, str(doc.get("hmac", ""))) \
        else ("untrusted", body)


# -- the split itself ------------------------------------------------------------------------------
def _copy(src: str, dst: str) -> None:
    for s in ("", "-journal", "-wal"):
        if os.path.isfile(src + s):
            shutil.copyfile(src + s, dst + s)
            with open(dst + s, "rb+") as fh:
                os.fsync(fh.fileno())


def _rows(con, table: str) -> Tuple[List[str], List[dict]]:
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
    con.row_factory = None
    return cols, rows


def _insert(con, table: str, row: dict) -> None:
    cols = list(row)
    con.execute(f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                f'VALUES ({",".join("?" * len(cols))})', [row[c] for c in cols])


class _Plan:
    """Where every row of the original goes (decided before anything is written)."""

    def __init__(self, data: Dict[str, List[dict]]):
        self.data = data
        con = {c["id"]: c for c in data["contributors"]}
        self.contrib = con
        local = [c["id"] for c in data["contributors"] if c["kind"] == "local_user"]
        self.local = local[0] if local else None
        claims = {c["id"]: c for c in data["claims"]}
        self.claims = claims
        self.shared_claims = {i for i, c in claims.items() if c["scope"] == "shared"}

    def person(self, cid: Optional[str]) -> Optional[str]:
        """The personal file a row of this contributor belongs to (SK's own
        and the importer's rows belong to the installation's local user)."""
        c = self.contrib.get(cid)
        if c is not None and c["kind"] in USERS:
            return cid
        return self.local

    def claim_home(self, claim_id) -> Optional[str]:
        """'shared' or the owner's contributor id."""
        if claim_id in self.shared_claims:
            return "shared"
        c = self.claims.get(claim_id)
        return self.person(c["owner_id"]) if c else self.local


def _place(data: Dict[str, List[dict]]):
    """{target: {table: [rows]}}, projections for shared.db, report notes."""
    plan = _Plan(data)
    out: Dict[str, Dict[str, List[dict]]] = {}
    notes: List[str] = []
    proj: Dict[str, List[dict]] = {"sources": [], "evidence": [], "contributors": []}
    withheld: Dict[str, List[int]] = {}

    def put(target, table, row):
        out.setdefault(target, {}).setdefault(table, []).append(dict(row))

    # proposal moment per shared claim: history before it stays with the owner
    proposed: Dict[int, int] = {}
    for e in data["claim_events"]:
        if e["claim_id"] in plan.shared_claims and e["event"] == "share_proposed":
            proposed.setdefault(e["claim_id"], e["id"])
    for c in data["claims"]:
        put(plan.claim_home(c["id"]), "claims", c)
    for e in data["claim_events"]:
        home = plan.claim_home(e["claim_id"])
        rel = e.get("related_claim")
        if home == "shared" and (e["id"] < proposed.get(e["claim_id"], 2 ** 62)
                                 or (rel is not None and rel not in plan.shared_claims)):
            home = plan.person(e["by"]) if plan.contrib.get(e["by"], {}).get("kind") in USERS \
                else plan.person(plan.claims[e["claim_id"]]["owner_id"])
            notes.append(f"claim_events {e['id']}: earlier or personal history of shared claim "
                         f"{e['claim_id']} kept with its person")
        put(home, "claim_events", e)
    for v in data["claim_versions"]:
        home = plan.claim_home(v["claim_id"])
        if home == "shared" and plan.contrib.get(v["replaced_by"], {}).get("kind") \
                not in MODERATOR_KINDS:
            home = plan.person(plan.claims[v["claim_id"]]["owner_id"])
        put(home, "claim_versions", v)
    src_by_id = {s["id"]: s for s in data["sources"]}
    shared_src = set()
    for ev in data["evidence"]:
        home = plan.claim_home(ev["claim_id"])
        if home == "shared" and ev["source_id"] is not None:
            s = src_by_id.get(ev["source_id"])
            if s is None or s["private"]:
                raise BrainError(f"shared claim {ev['claim_id']} has evidence from a private or "
                                 "missing source - not migrated")
            owner = plan.person(s["added_by"])
            put(owner, "evidence", ev)                     # the original, complete, with its owner
            p = dict(ev)
            p["locator"] = ""                              # the quote is withheld (no consent)
            proj["evidence"].append(p)
            shared_src.add(ev["source_id"])
            withheld.setdefault(owner, []).append(ev["id"])
        else:
            put(home, "evidence", ev)
    for s in data["sources"]:
        put(plan.person(s["added_by"]), "sources", s)
        if s["id"] in shared_src:
            proj["sources"].append(s)
    sec_home = {}
    for s in data["sections"]:
        h = plan.person(src_by_id[s["source_id"]]["added_by"]) if s["source_id"] in src_by_id \
            else plan.local
        sec_home[s["id"]] = h
        put(h, "sections", s)
    for ln in data["claim_links"]:
        if ln["from_claim"] in plan.shared_claims and ln["to_claim"] in plan.shared_claims and \
                plan.contrib.get(ln["added_by"], {}).get("kind") in MODERATOR_KINDS:
            put("shared", "claim_links", ln)
        else:
            put(plan.person(ln["added_by"]), "claim_links", ln)
    ents = {e["id"]: e for e in data["entities"]}
    for e in data["entities"]:
        put("shared" if e["scope"] == "shared" else plan.person(e["owner_id"]), "entities", e)
    for a in data["aliases"]:
        put("shared" if a["scope"] == "shared" else plan.person(a["owner_id"]), "aliases", a)
    for ln in data["entity_links"]:
        put("shared" if ln["scope"] == "shared" else plan.person(ln["owner_id"]), "entity_links",
            ln)
    for r in data["relations"]:
        if r["builtin"]:
            continue                                       # every file has the built-in ones
        kind = plan.contrib.get(r["added_by"], {}).get("kind")
        put("shared" if kind in MODERATOR_KINDS else plan.person(r["added_by"]), "relations", r)
    # contributors: each person's own full row in their file; shared.db gets SK's
    # own, moderators (named) and, for anyone else it mentions, an anonymous row
    for c in data["contributors"]:
        if c["kind"] in USERS:
            put(c["id"], "contributors", c)
        else:
            put("__all__", "contributors", c)
    return plan, out, proj, withheld, notes, ents, sec_home


def _contributors_mentioned(rows: Dict[str, List[dict]]) -> set:
    cols = ("owner_id", "created_by", "added_by", "by", "replaced_by", "removed_by")
    return {r[c] for t in rows.values() for r in t for c in cols if c in r and r[c]}


def split_file(src: str, data_dir: str, host_key: bytes, library_key: bytes,
               crash_at: str = "") -> dict:
    """Migrate `src` (a single-file knowledge database) into
    <data_dir>/brain. Never writes `src`. Returns the report; raises
    BrainError (staging removed, original and backup untouched) on failure."""
    crash_at = crash_at or os.environ.get("SK_TEST_CRASH_AT", "")

    def crash(point):
        if crash_at == point:
            os._exit(77)
    src = os.path.abspath(src)
    data_dir = os.path.abspath(data_dir)
    root = os.path.join(data_dir, "brain")
    if not os.path.isfile(src):
        raise BrainError("the database to migrate does not exist")
    if os.path.exists(root):
        raise BrainError("a brain folder exists already - nothing was changed")
    if any(n.startswith("brain-staging-") for n in os.listdir(data_dir)):
        raise BrainError("an unfinished migration was found - run recovery_check() first")
    need = sum(os.path.getsize(src + s) for s in ("",) + SIDECARS if os.path.isfile(src + s))
    if shutil.disk_usage(data_dir).free < 3 * need + (1 << 20):
        raise BrainError("not enough free disk space (about three times the database size)")
    before = fingerprint(src)
    try:
        version = peek_schema_version(src)
    except sqlite3.DatabaseError:
        raise BrainError("this is not an SK knowledge database (it cannot be read)")
    if version is None or version > SCHEMA_VERSION:
        raise BrainError("not an SK knowledge database of a known format")
    mig = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    backups = os.path.join(data_dir, "brain-backups", mig)
    anchor = os.path.join(data_dir, f"brain-anchor-{mig}.json")
    staging = os.path.join(data_dir, f"brain-staging-{mig}")
    os.makedirs(backups)
    # 1. backup (kept until the person deletes it)
    _copy(src, os.path.join(backups, "knowledge.db"))
    backup_fp = fingerprint(os.path.join(backups, "knowledge.db"))
    if not _same_content(before, backup_fp):
        raise BrainError("the backup does not match the original")
    # 2. manifests B and C, pending, with the original's and the backup's fingerprints
    start = {"migration_id": mig, "format": version, "layout": "b5.2-1", "created": _now_iso(),
             "original": {"path": src, "fingerprint": before},
             "backup": {"path": os.path.join(backups, "knowledge.db"),
                        "sha256": backup_fp["db"][2], "size": backup_fp["db"][0]}}
    for p in (os.path.join(backups, "manifest.json"), anchor):
        write_manifest(p, {"state": "pending", "start": start}, host_key)
    crash("after_manifests")
    tmp = tempfile.mkdtemp(prefix="sk_brain_split_")
    ok = False
    try:
        work = os.path.join(tmp, "work.db")
        _copy(src, work)
        if fingerprint(src) != before:
            raise BrainError("the original changed while it was being copied - close every "
                             "program using it and try again")
        if version < SCHEMA_VERSION:
            from .migrate import migrate_file
            up = os.path.join(tmp, "work.v4.db")
            migrate_file(work, up)
            work = up
        con = sqlite3.connect(work)
        try:
            data = {}
            cols = {}
            for t in TABLES:
                cols[t], data[t] = _rows(con, t)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
        report = _build(staging, data, cols, meta, host_key, library_key, mig, start, crash)
        if fingerprint(src) != before:
            raise BrainError("the original changed during the migration")
        crash("before_commit")
        os.replace(staging, root)                         # 6. THE commit point (one rename)
        crash("after_commit")
        commit = report["commit"]
        for p in (os.path.join(backups, "manifest.json"), anchor):
            write_manifest(p, {"state": "complete", "start": start, "commit": commit}, host_key)
        ok = True
        report.update({"migration_id": mig, "brain": root, "backup": backups, "anchor": anchor,
                       "original_unchanged": fingerprint(src) == before})
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if not ok and os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _build(staging, data, cols, meta, host_key, library_key, mig, start, crash) -> dict:
    plan, out, proj, withheld, notes, ents, sec_home = _place(data)
    lib = Library(staging, host_key, library_key)
    try:
        users: Dict[str, dict] = {}
        for c in data["contributors"]:
            if c["kind"] in USERS:
                uid = lib.create_user(c["display_name"] or "user", c["kind"], contributor_id=c["id"],
                                      pseudonym=c["id"])
                users[c["id"]] = lib._user(uid)
        files = {cid: lib.path("personal", u["file_id"] + ".db") for cid, u in users.items()}
        files["shared"] = lib.path("shared.db")
        allc = out.pop("__all__", {}).get("contributors", [])
        grants: Dict[str, List[Tuple[int, str, str]]] = {}
        opaque = {s["id"]: uuid.uuid4().hex for s in proj["sources"]}
        for target, tables in out.items():
            if target not in files:
                raise BrainError(f"rows without a person to keep them ({target})")
            con = sqlite3.connect(files[target])
            try:
                library_pragmas(con)
                con.execute("PRAGMA foreign_keys = OFF")
                for t in TABLES:
                    rows = tables.get(t, [])
                    if t == "contributors" and target != "shared":
                        rows = rows + allc
                    for r in rows:
                        if t in ("contributors", "relations"):
                            con.execute(f'DELETE FROM "{t}" WHERE '
                                        f'{"id" if t == "contributors" else "key"}=?',
                                        (r["id"] if t == "contributors" else r["key"],))
                        _insert(con, t, r)
                con.commit()
            finally:
                con.close()
        # shared.db: projections (same id, no personal content), contributors it mentions
        con = sqlite3.connect(files["shared"])
        try:
            library_pragmas(con)
            con.execute("PRAGMA foreign_keys = OFF")
            for s in proj["sources"]:
                stub = {k: None for k in s}
                stub.update({"id": s["id"], "kind": s["kind"], "added_at": s["added_at"],
                             "added_by": s["added_by"], "private": 0, "meta": "{}",
                             "content_hash": lib.tag(s["content_hash"] or opaque[s["id"]])})
                _insert(con, "sources", stub)
                con.execute("INSERT INTO source_opaque VALUES (?,?)", (s["id"], opaque[s["id"]]))
                tok = secrets.token_hex(32)
                con.execute("INSERT INTO source_tokens VALUES (?,?)",
                            (opaque[s["id"]], lib.token_hash(tok)))
                grants.setdefault(plan.person(s["added_by"]), []).append(
                    (s["id"], opaque[s["id"]], tok))
            for e in proj["evidence"]:
                _insert(con, "evidence", e)
            for t in ("sources", "evidence"):
                for r in proj[t]:
                    con.execute("INSERT INTO projections VALUES (?,?)", (t, r["id"]))
            shared_rows = dict(out.get("shared", {}))
            shared_rows["evidence"] = list(shared_rows.get("evidence", [])) + proj["evidence"]
            shared_rows["sources"] = proj["sources"]
            for cid in sorted(_contributors_mentioned(shared_rows)):
                c = plan.contrib.get(cid)
                if c is None:
                    continue
                named = c["kind"] in MODERATOR_KINDS or c["kind"] in ("system", "importer")
                con.execute("INSERT OR REPLACE INTO contributors VALUES (?,?,?,?)",
                            (c["id"], c["display_name"] if named else "contributor", c["kind"],
                             c["created"]))
            con.commit()
        finally:
            con.close()
        # the owners' own files: their withdrawal tokens (nothing granted: stub only)
        for owner, gl in grants.items():
            con = sqlite3.connect(files[owner])
            try:
                for sid, oid, tok in gl:
                    con.execute("INSERT INTO source_grants (source_id, opaque_id, token, levels) "
                                "VALUES (?,?,?,'[]')", (sid, oid, tok))
                con.commit()
            finally:
                con.close()
        crash("staging_rows")
        # derived data and cross-file references, per file (engine rules)
        st = KnowledgeStore(files["shared"], role="shared")
        st.close()
        xrefs = 0
        for cid, path in files.items():
            if cid == "shared":
                continue
            u = users[cid]
            st = KnowledgeStore(path, role="personal",
                                owner=(cid, u["display_name"], u["kind"]),
                                shared_path=files["shared"])
            try:
                xrefs += _record_xrefs(st)
            finally:
                st.close()
        _set_counters(lib, data, files)
        report = _verify(lib, data, cols, files, plan, users)
        report["notes"] = notes
        report["xrefs"] = xrefs
        report["withheld_quotes"] = {o: len(v) for o, v in withheld.items()}   # by person id
        crash("before_manifest_a")
        commit = {"files": {}, "users": {u["user_id"]: u["file_id"] for u in users.values()}}
        for name in _library_files(staging):
            p = os.path.join(staging, name)
            con = sqlite3.connect(p)
            try:
                checkpoint(con)
            finally:
                con.close()
        lib.close()
        lib = None
        for name in _library_files(staging):
            commit["files"][name] = {"sha256": sha256_file(os.path.join(staging, name)),
                                     "size": os.path.getsize(os.path.join(staging, name))}
        report["commit"] = commit
        write_manifest(os.path.join(staging, "manifest.json"),
                       {"state": "complete", "start": start, "commit": commit}, host_key)
        return report
    finally:
        if lib is not None:
            lib.close()


def _library_files(root: str) -> List[str]:
    out = []
    for d in ("", "personal", "outbox"):
        for n in sorted(os.listdir(os.path.join(root, d))):
            if n.endswith(".db"):
                out.append(os.path.join(d, n) if d else n)
    return out


def _record_xrefs(st: KnowledgeStore) -> int:
    """Rows of a personal file that point at the shared file: (namespace, id)."""
    refs = (("claims", "section_id", "sections"), ("evidence", "source_id", "sources"),
            ("evidence", "claim_id", "claims"), ("claim_links", "from_claim", "claims"),
            ("claim_links", "to_claim", "claims"), ("claim_events", "claim_id", "claims"),
            ("claim_events", "related_claim", "claims"), ("claim_versions", "claim_id", "claims"),
            ("aliases", "entity_id", "entities"), ("entity_links", "a", "entities"),
            ("entity_links", "b", "entities"))
    n = 0
    db = st._db
    for t, col, target in refs:
        for rid, ref in db.execute(f"SELECT id, {col} FROM main.{t} WHERE {col} IS NOT NULL "
                                   f"AND {col} NOT IN (SELECT id FROM main.{target})").fetchall():
            if db.execute(f"SELECT 1 FROM shared.{target} WHERE id=?", (ref,)).fetchone():
                db.execute("INSERT OR REPLACE INTO main.xrefs VALUES (?,?,?,?,?)",
                           (t, rid, col, st.shared_namespace, ref))
                n += 1
            else:                                         # another person's row: never reachable
                db.execute("INSERT OR REPLACE INTO main.xrefs VALUES (?,?,?,?,?)",
                           (t, rid, col, "inaccessible", ref))
    db.commit()
    return n


def _set_counters(lib: Library, data, files) -> None:
    hi = {t: max([r["id"] for r in data[t]] + [0]) for t in data if t not in ("contributors",
                                                                               "relations")}
    for t, v in hi.items():
        lib._reg.execute("INSERT OR REPLACE INTO counters VALUES (?,?)", (t, v + 1))
    lib._reg.commit()
    for cid, path in files.items():
        if cid == "shared":
            continue
        fid = os.path.basename(path)[:-3]
        con = sqlite3.connect(path)
        try:
            for t in hi:
                m = con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0]
                if m:
                    lib._record_hwm(fid, t, m)
        finally:
            con.close()


def _verify(lib, data, cols, files, plan, users) -> dict:
    """Every original row exactly once, unchanged; nothing personal in shared.db."""
    found: Dict[Tuple[str, object], int] = {}
    counts = {}
    shared = files["shared"]
    for cid, path in files.items():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BrainError(f"a new file failed its integrity check ({os.path.basename(path)})")
            proj = set()
            if cid == "shared":
                proj = {(r[0], r[1]) for r in con.execute("SELECT tbl, id FROM projections")}
            for t in TABLES:
                keyc = "key" if t == "relations" else "id"
                for r in con.execute(f'SELECT * FROM "{t}"'):
                    r = dict(r)
                    k = r[keyc]
                    if (t, k) in proj:
                        continue
                    if t in ("contributors", "relations"):
                        found.setdefault((t, k), 0)
                        continue
                    orig = _orig(data, t, k)
                    if orig is None:
                        raise BrainError(f"{t} row {k} does not come from the original")
                    if {c: r.get(c) for c in cols[t]} != orig:
                        raise BrainError(f"{t} row {k} differs from the original")
                    found[(t, k)] = found.get((t, k), 0) + 1
        finally:
            con.close()
    for t in TABLES:
        if t in ("contributors", "relations"):
            continue
        for r in data[t]:
            n = found.get((t, r["id"]), 0)
            if n != 1:
                raise BrainError(f"{t} row {r['id']} is in {n} files (must be exactly one)")
        counts[t] = len(data[t])
    _leak_check(shared, data, plan)
    return {"rows": counts, "users": len(users)}


def _orig(data, t, k):
    for r in data[t]:
        if r["id"] == k:
            return r
    return None


def _leak_check(shared: str, data, plan) -> None:
    con = sqlite3.connect(shared)
    try:
        bad = []
        bad += con.execute("SELECT 'claim', id FROM claims WHERE scope != 'shared'").fetchall()
        bad += con.execute("SELECT 'evidence', id FROM evidence WHERE claim_id NOT IN "
                           "(SELECT id FROM claims)").fetchall()
        bad += con.execute("SELECT 'event', id FROM claim_events WHERE claim_id NOT IN "
                           "(SELECT id FROM claims) OR (related_claim IS NOT NULL AND "
                           "related_claim NOT IN (SELECT id FROM claims))").fetchall()
        bad += con.execute("SELECT 'version', id FROM claim_versions WHERE claim_id NOT IN "
                           "(SELECT id FROM claims)").fetchall()
        bad += con.execute("SELECT 'source', id FROM sources WHERE private=1 OR title IS NOT NULL "
                           "OR reference IS NOT NULL OR raw_text IS NOT NULL").fetchall()
        bad += con.execute("SELECT 'section', id FROM sections").fetchall()
        bad += con.execute("SELECT 'quote', id FROM evidence WHERE source_id IS NOT NULL AND "
                           "locator != ''").fetchall()
        bad += con.execute("SELECT 'entity', id FROM entities WHERE scope != 'shared'").fetchall()
        bad += con.execute("SELECT 'alias', id FROM aliases WHERE scope != 'shared'").fetchall()
        bad += con.execute("SELECT 'meta', key FROM meta WHERE key IN ('local_user', 'owner')"
                           ).fetchall()
        bad += con.execute("SELECT 'contributor', id FROM contributors WHERE kind NOT IN "
                           "('system', 'importer', 'moderator') AND display_name != 'contributor'"
                           ).fetchall()
        if bad:
            raise BrainError(f"personal data would reach shared.db: {bad[:5]}")
    finally:
        con.close()


# -- write-ahead logs: valid, torn by a power cut, or damaged --------------------------------------------
WAL_MAGIC = (0x377F0682, 0x377F0683)


def _wal_sum(data: bytes, big: bool, s0: int = 0, s1: int = 0):
    import struct
    fmt = (">" if big else "<") + "%dI" % (len(data) // 4)
    w = struct.unpack(fmt, data)
    for i in range(0, len(w), 2):
        s0 = (s0 + w[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + w[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def wal_state(path: str) -> str:
    """'absent' / 'empty' / 'ok' / 'torn_tail' / 'damaged' for <db>-wal.
    SQLite silently ignores log frames that fail their checksum, so a damaged
    log could lose committed data without any error - this reads the log the
    way SQLite does and tells the cases apart:
      ok         every frame of the current log generation checks out
      torn_tail  an incomplete or failing frame AFTER the last commit (a power
                 cut while writing: that data was never committed)
      damaged    a bad header, or a failing frame followed by a commit frame
                 (committed data would be lost)."""
    import struct
    if not os.path.isfile(path):
        return "absent"
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        return "empty"
    if len(data) < 32:
        return "damaged"
    magic, _ver, psize, _seq, salt1, salt2, c1, c2 = struct.unpack(">8I", data[:32])
    if magic not in WAL_MAGIC or psize < 512 or psize & (psize - 1):
        return "damaged"
    big = magic & 1 == 1
    if _wal_sum(data[:24], big) != (c1, c2):
        return "damaged"
    s0, s1 = c1, c2
    off, fsize = 32, 24 + psize
    while off + 24 <= len(data):
        pg, dbsize, fs1, fs2, k1, k2 = struct.unpack(">6I", data[off:off + 24])
        if (fs1, fs2) != (salt1, salt2):
            return "ok"                      # older frames of a previous generation: ignored
        if off + fsize > len(data):
            return "torn_tail"               # an incomplete last frame
        t0, t1 = _wal_sum(data[off:off + 8], big, s0, s1)
        t0, t1 = _wal_sum(data[off + 24:off + fsize], big, t0, t1)
        if (t0, t1) != (k1, k2):
            # committed data after this point would be lost: damaged; otherwise torn
            o = off
            while o + 24 <= len(data):
                _p, ds, a, b, _x, _y = struct.unpack(">6I", data[o:o + 24])
                if (a, b) != (salt1, salt2):
                    break
                if ds:
                    return "damaged"
                o += fsize
            return "torn_tail"
        s0, s1 = t0, t1
        off += fsize
    return "ok" if off == len(data) else "torn_tail"


def _check_with_log(path: str) -> Tuple[str, str]:
    """(state, why) of a database together with its write-ahead log, checked
    on a TEMPORARY copy of both (the files themselves are never opened)."""
    w = wal_state(path + "-wal")
    if w == "damaged":
        return "damaged", "its write-ahead log is damaged (committed changes would be lost)"
    ok, _ = _integrity_on_copy(path, with_log=True)      # the file + its log, together
    if not ok:
        return "damaged", "it fails its integrity check (with its write-ahead log applied)"
    return "ok", w


# -- opening a migrated library: checked against its record first ------------------------------------
def verify_library(root: str, host_key: bytes) -> str:
    """Before the first use after a migration: every file must match manifest
    A's commit record (and manifests B / C are finished if a crash left them
    pending). Raises RecoveryNeeded otherwise - nothing is opened or changed."""
    trust, body = read_manifest(os.path.join(root, "manifest.json"), host_key)
    if trust == "missing":
        if os.path.isfile(os.path.join(root, "registry.db")):
            con = sqlite3.connect(file_uri(os.path.join(root, "registry.db"), "ro"), uri=True)
            try:
                migrated = con.execute("SELECT value FROM meta WHERE key='migration_verified'"
                                       ).fetchone()
            finally:
                con.close()
            if migrated:
                raise RecoveryNeeded("the migration record of this brain folder is missing")
        return "no migration record"
    if trust != "authentic" or body.get("state") != "complete":
        raise RecoveryNeeded("the migration record in the brain folder cannot be trusted")
    for name, rec in body["commit"]["files"].items():
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            raise RecoveryNeeded(f"{name} is missing")
        pending = os.path.isfile(p + "-wal") and os.path.getsize(p + "-wal") > 0
        if pending or sha256_file(p) != rec["sha256"]:
            # writes since the migration (in the file or still in its log) are normal;
            # corruption is not: the file and its log are checked TOGETHER, on a copy
            state, why = _check_with_log(p)
            if state != "ok":
                raise RecoveryNeeded(f"{name}: {why}")
    data_dir = os.path.dirname(os.path.abspath(root))
    mig = body["start"]["migration_id"]
    reg = sqlite3.connect(file_uri(os.path.join(root, "registry.db"), "rw"), uri=True)
    try:
        r = reg.execute("SELECT value FROM meta WHERE key='migration_verified'").fetchone()
        if not (r and r[0] == mig):
            reg.execute("INSERT OR REPLACE INTO meta VALUES ('migration_verified', ?)", (mig,))
            reg.commit()
    finally:
        reg.close()
    for p in (os.path.join(data_dir, "brain-backups", mig, "manifest.json"),
              os.path.join(data_dir, f"brain-anchor-{mig}.json")):
        t, b = read_manifest(p, host_key)
        if t == "authentic" and b.get("state") == "pending" and b["start"] == body["start"]:
            write_manifest(p, body, host_key)             # the crash window: finish B / C
    return "verified"


# -- recovery: read-only check, options, never automatic deletion ------------------------------------
def _integrity_on_copy(path: str, with_log: bool = True, probe: Optional[str] = None):
    """PRAGMA integrity_check on a TEMPORARY byte copy of the database AND (by
    default) its write-ahead log, so SQLite checks the combined state - the
    pages committed in the log included. The originals are only read as bytes.
    Returns (ok, probe result) - ok None if the database is missing; `probe`
    (tests) is one query run on the same combined copy."""
    if not os.path.isfile(path):
        return None, None
    tmp = tempfile.mkdtemp(prefix="sk_brain_check_")
    try:
        c = os.path.join(tmp, "c.db")
        shutil.copyfile(path, c)
        log = path + "-wal"
        if with_log and os.path.isfile(log) and os.path.getsize(log) > 0:
            shutil.copyfile(log, c + "-wal")
            if sha256_file(c + "-wal") != sha256_file(log):
                raise BrainError("the write-ahead log changed while it was being copied")
        try:
            con = sqlite3.connect(c)
            try:
                ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                out = con.execute(probe).fetchall() if probe else None
                return ok, out
            finally:
                con.close()
        except sqlite3.DatabaseError:
            return False, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _db_ok(path: str) -> Optional[bool]:
    """Integrity of a database TOGETHER WITH its write-ahead log, checked on a
    temporary copy of both (None: missing)."""
    return _integrity_on_copy(path)[0]


def recovery_check(data_dir: str, host_key: Optional[bytes]) -> dict:
    """Check every copy independently (byte copies only; nothing is opened in
    place, nothing is changed) and work out the safe options. Writes a plain
    report OUTSIDE the brain folder."""
    data_dir = os.path.abspath(data_dir)
    root = os.path.join(data_dir, "brain")
    migs = {n[len("brain-anchor-"):-5] for n in os.listdir(data_dir)
            if n.startswith("brain-anchor-") and n.endswith(".json")}
    if os.path.isdir(os.path.join(data_dir, "brain-backups")):
        migs |= set(os.listdir(os.path.join(data_dir, "brain-backups")))
    migs = sorted(migs)
    staging = sorted(n for n in os.listdir(data_dir) if n.startswith("brain-staging-"))
    mig = migs[-1] if migs else (staging[-1][len("brain-staging-"):] if staging else None)
    copies = {}
    if mig:
        copies["B"] = read_manifest(os.path.join(data_dir, "brain-backups", mig, "manifest.json"),
                                    host_key)
        copies["C"] = read_manifest(os.path.join(data_dir, f"brain-anchor-{mig}.json"), host_key)
    copies["A"] = read_manifest(os.path.join(root, "manifest.json"), host_key)
    rep = {"migration_id": mig, "manifests": {k: v[0] for k, v in copies.items()},
           "states": {k: (v[1] or {}).get("state") for k, v in copies.items()},
           "anomalies": [], "options": ["keep_everything"]}
    authentic = {k: b for k, (t, b) in copies.items() if t == "authentic"}
    intact = {k: b for k, (t, b) in copies.items() if t == "unauthenticated"}
    # compare overlapping fields: start parts always; commit parts between complete copies
    for k, b in list(authentic.items()) + list(intact.items()):
        if mig and b.get("start", {}).get("migration_id") != mig:
            rep["anomalies"].append(f"manifest {k} belongs to another migration")
    starts = {json.dumps(b.get("start"), sort_keys=True) for b in authentic.values()}
    commits = {json.dumps(b.get("commit"), sort_keys=True) for b in authentic.values()
               if b.get("state") == "complete"}
    if len(starts) > 1 or len(commits) > 1:
        rep["anomalies"].append("the authentic manifests disagree")
    for k, b in intact.items():
        if authentic and json.dumps(b.get("start"), sort_keys=True) not in starts:
            rep["anomalies"].append(f"manifest {k} (not authenticated) contradicts the others")
    for k, b in list(authentic.items()) + list(intact.items()):
        if b.get("state") not in ("pending", "complete"):
            rep["anomalies"].append(f"manifest {k} has an unknown state")
    trusted = authentic and not rep["anomalies"]
    expected = next(iter(authentic.values()))["start"] if trusted else None
    complete = next((b for b in authentic.values() if b.get("state") == "complete"), None) \
        if trusted else None
    # the original and the backup, independently
    orig_path = expected["original"]["path"] if expected else None
    bak_path = os.path.join(data_dir, "brain-backups", mig, "knowledge.db") if mig else None
    o = {"present": bool(orig_path and os.path.isfile(orig_path))}
    if o["present"]:
        fp = fingerprint(orig_path)
        o["matches_record"] = _same_content(fp, expected["original"]["fingerprint"])
        o["integrity"] = _db_ok(orig_path)
    b = {"present": bool(bak_path and os.path.isfile(bak_path))}
    if b["present"]:
        b["sha256"] = sha256_file(bak_path)
        b["matches_record"] = bool(expected) and b["sha256"] == expected["backup"]["sha256"]
        b["integrity"] = _db_ok(bak_path)
    rep["original"], rep["backup"] = o, b
    verified_copy = (o.get("matches_record") and o.get("integrity")) or \
        (b.get("matches_record") and b.get("integrity"))
    rep["verified_copy"] = bool(verified_copy)
    if not trusted and o.get("present") and b.get("present"):
        rep["original_equals_backup"] = sha256_file(orig_path) == b["sha256"] \
            if orig_path else None
    # the brain folder, file by file
    files = {}
    new_data_possible = True
    if os.path.isdir(root):
        names = _library_files(root)
        rec = (complete or {}).get("commit", {}).get("files", {})
        unchanged = True
        for name in sorted(set(names) | set(rec)):
            p = os.path.join(root, name)
            st = {"present": os.path.isfile(p)}
            if st["present"]:
                state, why = _check_with_log(p)
                st["integrity"] = state == "ok"
                st["log"] = why if state == "ok" else "damaged"
                wal = p + "-wal"
                st["changed_since_migration"] = (name not in rec or sha256_file(p) !=
                                                 rec[name]["sha256"] or
                                                 (os.path.isfile(wal) and os.path.getsize(wal) > 0))
            if not st["present"] or st.get("changed_since_migration") or not st.get("integrity"):
                unchanged = unchanged and not st.get("changed_since_migration", False)
            files[name] = st
        # only an AUTHENTIC COMPLETE record can show that no new data was written
        new_data_possible = not (complete and all(
            not f.get("changed_since_migration") for f in files.values() if f["present"]))
    rep["brain_files"] = files
    rep["new_data_possible"] = new_data_possible
    damaged = os.path.isdir(root) and (copies["A"][0] != "authentic" or any(
        not f["present"] or not f.get("integrity") for f in files.values()))
    rep["damage_found"] = bool(damaged)
    # options (never automatic)
    if verified_copy:
        rep["options"].append("rebuild_from_verified_copy")
    elif o.get("present") or b.get("present"):
        rep["options"].append("rebuild_from_original_unverified")
    if os.path.isdir(root):
        rep["options"] += ["set_brain_aside", "rescue_readable_rows"]
    if damaged and trusted and complete and verified_copy and not new_data_possible:
        rep["options"].append("delete_damaged_brain")
    if staging:
        rep["staging"] = staging
        if trusted and o.get("matches_record"):
            rep["options"].append("remove_unfinished_staging")
    path = os.path.join(data_dir, f"brain-recovery-{_dt.datetime.now():%Y%m%d-%H%M%S}.json")
    _atomic_write(path, json.dumps(rep, indent=1, sort_keys=True, default=str).encode())
    rep["report"] = path
    return rep


def remove_unfinished_staging(data_dir: str, host_key: bytes) -> bool:
    """Deletes a leftover staging folder ONLY when the original verifies
    against an authentic manifest (B or C); otherwise keeps it."""
    rep = recovery_check(data_dir, host_key)
    if "remove_unfinished_staging" not in rep["options"]:
        return False
    for n in rep.get("staging", []):
        shutil.rmtree(os.path.join(data_dir, n), ignore_errors=True)
    return True


def set_brain_aside(data_dir: str) -> str:
    """Rename (never delete) the brain folder to brain-damaged-<time>."""
    root = os.path.join(data_dir, "brain")
    dst = os.path.join(data_dir, f"brain-damaged-{_dt.datetime.now():%Y%m%d-%H%M%S}")
    os.replace(root, dst)
    return dst


def delete_damaged_brain(data_dir: str, host_key: bytes, confirm: bool = False) -> bool:
    """Only when recovery_check offers it AND the person confirms."""
    if not confirm:
        return False
    rep = recovery_check(data_dir, host_key)
    if "delete_damaged_brain" not in rep["options"]:
        return False
    shutil.rmtree(os.path.join(data_dir, "brain"))
    return True


def rescue_readable_rows(data_dir: str) -> dict:
    """Copy every row still readable from byte copies of the brain files into
    brain-rescued-<time>/ (same file names). Unreadable tables are listed."""
    root = os.path.join(data_dir, "brain")
    dst = os.path.join(data_dir, f"brain-rescued-{_dt.datetime.now():%Y%m%d-%H%M%S}")
    lost = {}
    tmp = tempfile.mkdtemp(prefix="sk_brain_rescue_")
    try:
        for name in _library_files(root):
            c = os.path.join(tmp, "c.db")
            for s in ("", "-wal", "-shm"):
                if os.path.exists(c + s):
                    os.remove(c + s)
            _copy(os.path.join(root, name), c)
            out = os.path.join(dst, name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            try:
                src = sqlite3.connect(c)
                tables = [r[0] for r in src.execute("SELECT name, sql FROM sqlite_master WHERE "
                                                    "type='table' AND sql IS NOT NULL AND name "
                                                    "NOT LIKE 'claim_fts%'")]
                dcon = sqlite3.connect(out)
                for t in tables:
                    try:
                        sql = src.execute("SELECT sql FROM sqlite_master WHERE name=?",
                                          (t,)).fetchone()[0]
                        dcon.execute(sql)
                        rows = src.execute(f'SELECT * FROM "{t}"').fetchall()
                        if rows:
                            dcon.executemany(f'INSERT INTO "{t}" VALUES '
                                             f'({",".join("?" * len(rows[0]))})', rows)
                    except sqlite3.DatabaseError as exc:
                        lost.setdefault(name, []).append(f"{t}: {exc}")
                dcon.commit()
                dcon.close()
                src.close()
            except sqlite3.DatabaseError as exc:
                lost[name] = [f"unreadable: {exc}"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"rescued_to": dst, "lost": lost}
