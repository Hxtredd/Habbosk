"""B6: upgrading a B5.2 library to the B6 format - as a verified COPY, never in place.

    report = upgrade_library(data_dir, host_key)      # SK Assistant closed
    state  = recover_upgrade(data_dir, host_key)      # after any interruption

<data_dir>/brain is never changed in place. The steps, each recorded in a signed
journal (checksum + HMAC with the host key) kept in TWO places outside brain\\:
<data_dir>/brain-upgrade-<id>.json and brain-backups/pre-b6-<id>/upgrade.json.

  1. fingerprints   every file of brain\\ (size + SHA-256), sidecars included
  2. backup         brain-backups/pre-b6-<id>/brain  (byte copy, checked against 1;
                    brain\\ must not change while it is copied)
  3. journal        "started"
  4. staging        brain-upgrade-staging-<id>  copied from the VERIFIED backup
  5. upgrade        each file of the copy in its own role, one transaction each:
                    personal files get the personal tables, shared.db the shared
                    ones, outboxes theirs; the registry gets the new format LAST.
                    No data moves between files.
  6. verification   every row of every existing table identical to the backup,
                    new tables empty, other files byte-identical, meta unchanged
                    except the format, integrity, shared.db leak scan -> "verified"
  7. swap           "swapping"; rename brain -> brain-pre-b6-<id>; rename staging
                    -> brain; "done"
Nothing is deleted automatically, ever: the old library (brain-pre-b6-<id>) and
the backup stay until the person removes them. recover_upgrade() decides from
the folders actually present and their fingerprints - never from the journal
alone - and always leaves a complete original or a verified upgraded library in
place as brain\\.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sqlite3
import uuid
from typing import Dict, Optional, Tuple

from .schema import LAYOUT, OUTBOX_B6_SCHEMA
from .store import BrainError, install_b6, library_pragmas
from .split import read_manifest, sha256_file, write_manifest

B6_TABLES = {"shared": {"source_versions", "passages", "passage_index", "passage_facts",
                        "evidence_locations"},   # (+ the passage_fts search index)
             "outbox": {"material_versions"}, "registry": set()}
B6_TABLES["personal"] = B6_TABLES["shared"] | {"source_files", "claim_candidates"}
FINAL = ("done", "abandoned", "rolled_back")


class UpgradePending(BrainError):
    """An upgrade was interrupted: run recover_upgrade() before using the library."""


# -- fingerprints ----------------------------------------------------------------------
def tree_fingerprint(root: str) -> Dict[str, list]:
    """(size, SHA-256) of every file under `root`. SQLite's shared-memory files
    (-shm) are left out: they hold no data and SQLite rebuilds them."""
    out = {}
    for d, _, names in os.walk(root):
        for n in sorted(names):
            if n.endswith("-shm"):
                continue
            p = os.path.join(d, n)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = [os.path.getsize(p),
                                                                  sha256_file(p)]
    return out


def _copy_tree(src: str, dst: str) -> None:
    for d, _, names in os.walk(src):
        rel = os.path.relpath(d, src)
        os.makedirs(os.path.join(dst, rel) if rel != "." else dst, exist_ok=True)
        for n in names:
            out = os.path.join(dst, rel, n) if rel != "." else os.path.join(dst, n)
            shutil.copyfile(os.path.join(d, n), out)
            with open(out, "rb+") as fh:
                os.fsync(fh.fileno())


def _role(rel: str) -> Optional[str]:
    if not rel.endswith(".db"):
        return None
    if rel == "registry.db":
        return "registry"
    if rel == "shared.db":
        return "shared"
    if rel.startswith("personal/"):
        return "personal"
    if rel.startswith("outbox/"):
        return "outbox"
    return None


# -- journal -------------------------------------------------------------------------------
def _journals(data_dir: str, uid: str) -> Tuple[str, str]:
    return (os.path.join(data_dir, f"brain-upgrade-{uid}.json"),
            os.path.join(data_dir, "brain-backups", f"pre-b6-{uid}", "upgrade.json"))


def _record(data_dir: str, body: dict, host_key: bytes) -> None:
    for p in _journals(data_dir, body["upgrade_id"]):
        write_manifest(p, body, host_key)


def unfinished(data_dir: str) -> list:
    """Upgrade ids whose journal (outside brain\\) is not in a final state."""
    out = []
    if not os.path.isdir(data_dir):
        return out
    for n in os.listdir(data_dir):
        if n.startswith("brain-upgrade-") and n.endswith(".json"):
            with open(os.path.join(data_dir, n), encoding="utf-8") as fh:
                try:
                    state = json.load(fh).get("body", {}).get("state")
                except ValueError:
                    state = None
            if state not in FINAL:
                out.append(n[len("brain-upgrade-"):-5])
    return sorted(out)


# -- upgrade --------------------------------------------------------------------------------
def _upgrade_file(path: str, role: str) -> None:
    con = sqlite3.connect(path, isolation_level=None)
    try:
        library_pragmas(con)
        con.execute("BEGIN IMMEDIATE")
        try:
            if role in ("personal", "shared"):
                install_b6(con, role)
            elif role == "outbox":
                for stmt in OUTBOX_B6_SCHEMA.split(";"):
                    if stmt.strip():
                        con.execute(stmt)
                con.execute("INSERT OR REPLACE INTO meta VALUES ('layout', ?)", (LAYOUT,))
            elif role == "registry":
                con.execute("INSERT OR REPLACE INTO meta VALUES ('layout', ?)", (LAYOUT,))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def _tables(con) -> Dict[str, str]:
    return {r[0]: r[1] for r in con.execute(
        "SELECT name, type FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _verify(backup: str, staging: str) -> Dict[str, list]:
    """Every row of every existing table identical; the new tables empty and
    only where they belong; other files byte-identical. The backup is read from
    a TEMPORARY copy (opening it would let SQLite fold its log into it). Returns
    the staging fingerprints, taken after the checks."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="sk_brain_upgrade_check_")
    try:
        ref = os.path.join(tmp, "ref")
        _copy_tree(backup, ref)
        _verify_against(ref, staging)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tree_fingerprint(staging)


def _verify_against(backup: str, staging: str) -> None:
    b, s = tree_fingerprint(backup), tree_fingerprint(staging)
    bdb = {k for k in b if not k.endswith(("-wal", "-shm", "-journal"))}
    sdb = {k for k in s if not k.endswith(("-wal", "-shm", "-journal"))}
    if bdb != sdb:
        raise BrainError(f"the upgraded copy has other files ({sorted(bdb ^ sdb)[:5]})")
    for rel in sorted(bdb):
        role = _role(rel)
        if role is None:
            if b[rel] != s[rel]:
                raise BrainError(f"{rel} differs in the upgraded copy")
            continue
        old = sqlite3.connect(os.path.join(backup, rel))
        new = sqlite3.connect(os.path.join(staging, rel))
        try:
            if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BrainError(f"{rel}: the upgraded copy fails its integrity check")
            ot, nt = _tables(old), _tables(new)
            for t in ot:
                if t not in nt:
                    raise BrainError(f"{rel}: table {t} is missing after the upgrade")
                cols = [r[1] for r in old.execute(f'PRAGMA table_info("{t}")')]
                q = f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"'
                try:                                   # tables WITHOUT ROWID: compared sorted
                    a = old.execute(q + " ORDER BY rowid").fetchall()
                    z = new.execute(q + " ORDER BY rowid").fetchall()
                except sqlite3.OperationalError:
                    a = sorted(old.execute(q).fetchall(), key=repr)
                    z = sorted(new.execute(q).fetchall(), key=repr)
                if t == "meta":
                    a = sorted(r for r in a if r[0] != "layout")
                    z = sorted(r for r in z if r[0] != "layout")
                if a != z:
                    raise BrainError(f"{rel}: table {t} differs after the upgrade")
            added = {t for t in nt if t not in ot and not t.startswith("passage_fts")}
            if added != B6_TABLES[role]:
                raise BrainError(f"{rel}: unexpected new tables {sorted(added ^ B6_TABLES[role])}")
            for t in added:
                if new.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]:
                    raise BrainError(f"{rel}: new table {t} is not empty")
            lay = new.execute("SELECT value FROM meta WHERE key='layout'").fetchone()
            if lay != (LAYOUT,):
                raise BrainError(f"{rel}: the format was not set")
            if role == "shared":
                shared_scan(new)
        finally:
            old.close()
            new.close()


def shared_scan(con) -> None:
    """A LIVE shared.db holds nothing personal: shared claims only; no private
    source; source content (title / reference / text) and evidence quotes only
    for sources an owner granted (source_opaque) and has not withdrawn; no
    owner metadata; no personal-only tables; names only of moderators / SK."""
    granted = ("id IN (SELECT o.source_id FROM source_opaque o WHERE o.opaque_id NOT IN "
               "(SELECT opaque_id FROM withdrawals))")
    bad = []
    bad += con.execute("SELECT 'claim', id FROM claims WHERE scope != 'shared'").fetchall()
    bad += con.execute("SELECT 'private source', id FROM sources WHERE private=1").fetchall()
    bad += con.execute("SELECT 'source content', id FROM sources WHERE (title IS NOT NULL OR "
                       f"reference IS NOT NULL OR raw_text IS NOT NULL) AND NOT {granted}"
                       ).fetchall()
    bad += con.execute("SELECT 'quote', e.id FROM evidence e WHERE e.locator != '' AND "
                       "e.source_id IS NOT NULL AND e.source_id NOT IN (SELECT id FROM sources "
                       f"WHERE {granted})").fetchall()
    bad += con.execute("SELECT 'meta', key FROM meta WHERE key IN ('local_user', 'owner')"
                       ).fetchall()
    bad += con.execute("SELECT 'contributor', id FROM contributors WHERE kind NOT IN "
                       "('system', 'importer', 'moderator') AND display_name != 'contributor'"
                       ).fetchall()
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    bad += [("personal-only table", t) for t in ("source_files", "claim_candidates",
                                                 "source_grants") if t in names and
            con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]]
    if bad:
        raise BrainError(f"personal data found in shared.db: {bad[:5]}")


def upgrade_library(data_dir: str, host_key: bytes, crash_at: str = "") -> dict:
    """Upgrade <data_dir>/brain (B5.2) into the B6 format (see the module text)."""
    crash_at = crash_at or os.environ.get("SK_TEST_CRASH_AT", "")

    def crash(point):
        if crash_at == point:
            os._exit(77)
    data_dir = os.path.abspath(data_dir)
    root = os.path.join(data_dir, "brain")
    if not os.path.isdir(root):
        raise BrainError("there is no library to upgrade")
    if unfinished(data_dir):
        raise UpgradePending("an earlier upgrade was interrupted - run recover_upgrade() first")
    reg = os.path.join(root, "registry.db")
    from .store import file_uri
    con = sqlite3.connect(file_uri(reg, "ro") + "&immutable=1", uri=True)
    try:
        lay = con.execute("SELECT value FROM meta WHERE key='layout'").fetchone()
    finally:
        con.close()
    if lay == (LAYOUT,):
        raise BrainError("the library is already in the B6 format - nothing to do")
    need = sum(v[0] for v in tree_fingerprint(root).values())
    if shutil.disk_usage(data_dir).free < 3 * need + (1 << 20):
        raise BrainError("not enough free disk space (about three times the library's size)")
    uid = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    backup = os.path.join(data_dir, "brain-backups", f"pre-b6-{uid}", "brain")
    staging = os.path.join(data_dir, f"brain-upgrade-staging-{uid}")
    old = os.path.join(data_dir, f"brain-pre-b6-{uid}")
    before = tree_fingerprint(root)
    _copy_tree(root, backup)                                        # 2. backup
    if tree_fingerprint(backup) != before or tree_fingerprint(root) != before:
        shutil.rmtree(os.path.dirname(backup), ignore_errors=True)  # not a copy of anything
        raise BrainError("the library changed while it was being copied - close SK Assistant "
                         "and try again (nothing was changed)")
    crash("after_backup")
    body = {"upgrade_id": uid, "state": "started", "from": "b5.2", "to": LAYOUT,
            "created": _dt.datetime.now().isoformat(timespec="seconds"), "before": before,
            "backup": backup, "staging": staging, "old": old}
    _record(data_dir, body, host_key)                               # 3. journal
    crash("after_journal_started")
    _copy_tree(backup, staging)                                     # 4. staging
    crash("staging_copied")
    names = sorted(before, key=lambda r: (r == "registry.db", r))   # the registry LAST
    first = True
    for rel in names:                                               # 5. upgrade
        role = _role(rel)
        if role is None:
            continue
        _upgrade_file(os.path.join(staging, rel), role)
        if first:
            crash("after_first_file")
            first = False
    for d, _, ns in os.walk(staging):                               # no sidecars left
        for n in ns:
            if n.endswith("-shm"):
                os.remove(os.path.join(d, n))
    body["staged"] = _verify(backup, staging)                       # 6. verification
    body["state"] = "verified"
    _record(data_dir, body, host_key)
    crash("after_verified")
    body["state"] = "swapping"                                      # 7. swap
    _record(data_dir, body, host_key)
    crash("after_swapping")
    os.replace(root, old)
    crash("between_renames")
    os.replace(staging, root)
    crash("after_second_rename")
    body["state"] = "done"
    _record(data_dir, body, host_key)
    return {"upgrade_id": uid, "backup": backup, "old_library": old, "files": len(before)}


# -- recovery --------------------------------------------------------------------------------
def _journal(data_dir: str, uid: str, host_key: bytes):
    """The journal body if at least one copy is authentic and the authentic
    copies agree on everything but the state; else None."""
    bodies = []
    for p in _journals(data_dir, uid):
        trust, body = read_manifest(p, host_key)
        if trust == "authentic":
            bodies.append(body)
    if not bodies:
        return None
    strip = [{k: v for k, v in b.items() if k not in ("state", "staged")} for b in bodies]
    if any(x != strip[0] for x in strip):
        return None
    order = ("started", "verified", "swapping", "done", "abandoned", "rolled_back")
    # the copies are written one after the other: a crash in between leaves them a
    # step apart - the EARLIER state is the safe one to assume
    best = min(bodies, key=lambda b: order.index(b.get("state")) if b.get("state") in order
               else -1)
    staged = next((b.get("staged") for b in bodies if b.get("staged")), None)
    return dict(best, staged=staged)


def recover_upgrade(data_dir: str, host_key: Optional[bytes]) -> dict:
    """Finish or undo an interrupted upgrade, deciding from the folders present
    and their fingerprints. Never deletes the old library or the backup; deletes
    a staging copy only when the original library verifies unchanged."""
    data_dir = os.path.abspath(data_dir)
    out = {"recovered": [], "report_only": []}
    for uid in unfinished(data_dir):
        body = _journal(data_dir, uid, host_key) if host_key else None
        if body is None:
            out["report_only"].append({"upgrade_id": uid, "why": "the journal cannot be "
                                       "authenticated or its copies disagree - nothing was "
                                       "changed; the backup is in brain-backups"})
            continue
        root = os.path.join(data_dir, "brain")
        R, O, S = os.path.isdir(root), os.path.isdir(body["old"]), os.path.isdir(body["staging"])
        before, staged = body["before"], body.get("staged")

        def ok(path, want):
            return want is not None and os.path.isdir(path) and tree_fingerprint(path) == want

        def finish(state, action):
            body["state"] = state
            body.pop("staged", None) if not staged else body.update({"staged": staged})
            _record(data_dir, body, host_key)
            out["recovered"].append({"upgrade_id": uid, "action": action})
        if R and not O:
            # the swap never started: the original is still brain\
            if ok(root, before):
                if S:
                    shutil.rmtree(body["staging"])        # a copy; the original is intact
                finish("abandoned", "upgrade not applied; the original library is intact")
            else:
                out["report_only"].append({"upgrade_id": uid, "why": "the library differs from "
                                           "the recorded original - nothing was changed"})
        elif not R and O and S:
            # between the two renames
            if ok(body["staging"], staged):
                os.replace(body["staging"], root)
                finish("done", "the verified upgraded library was put in place")
            elif ok(body["old"], before):
                os.replace(body["old"], root)
                finish("rolled_back", "the original library was put back")
            else:
                out["report_only"].append({"upgrade_id": uid, "why": "neither copy verifies - "
                                           "nothing was moved; the backup is in brain-backups"})
        elif R and O and not S:
            # after the second rename
            if ok(root, staged):
                finish("done", "the upgrade had finished; recorded")
            else:
                out["report_only"].append({"upgrade_id": uid, "why": "the library in place does "
                                           "not match the verified upgrade - nothing was "
                                           "changed"})
        elif not R and O and not S:
            if ok(body["old"], before):
                os.replace(body["old"], root)
                finish("rolled_back", "the original library was put back")
            else:
                out["report_only"].append({"upgrade_id": uid, "why": "the original library does "
                                           "not verify - nothing was moved"})
        else:
            out["report_only"].append({"upgrade_id": uid, "why": "unexpected folders - nothing "
                                       "was changed; the backup is in brain-backups"})
    return out


# -- SK-managed backups (B6 safeguard: withdrawn / purged text must not linger) -----------
def backups(data_dir: str) -> list:
    """Every SK-managed copy that may hold older library content: the B6 upgrade's
    backup and old library, and the B5.2 migration backups (brain-backups/<id>)."""
    out = []
    root = os.path.join(data_dir, "brain-backups")
    if os.path.isdir(root):
        for n in sorted(os.listdir(root)):
            out.append(os.path.join(root, n))
    for n in sorted(os.listdir(data_dir)) if os.path.isdir(data_dir) else []:
        if n.startswith("brain-pre-b6-") or n.startswith("brain-upgrade-staging-"):
            out.append(os.path.join(data_dir, n))
    return out


def backups_holding(data_dir: str, needles) -> list:
    """The SK-managed backups in which any of `needles` (bytes) is found, byte
    for byte (sidecars included). Empty needles: every backup that exists, as
    it may hold an earlier copy."""
    found = []
    for b in backups(data_dir):
        hit = not needles
        for d, _, names in os.walk(b):
            for n in names:
                if hit:
                    break
                try:
                    with open(os.path.join(d, n), "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                hit = any(x in data for x in needles)
        if hit:
            found.append(os.path.relpath(b, data_dir).replace(os.sep, "/"))
    return found


def release_backups(data_dir: str, host_key: bytes, library_key: bytes,
                    confirm: bool = False) -> list:
    """Delete the SK-managed backups - only when the person confirms AND the
    current library is complete and verified (no unfinished upgrade, it opens,
    every database passes an integrity check), so the only known-good copy is
    never deleted. Returns what was removed."""
    if confirm is not True:
        raise BrainError("releasing backups deletes them for good: call with confirm=True")
    if unfinished(data_dir):
        raise UpgradePending("an upgrade is unfinished: run recover_upgrade() first")
    root = os.path.join(data_dir, "brain")
    from .library import Library
    lib = Library(root, host_key, library_key)          # refuses an old / damaged library
    lib.close()
    for rel in tree_fingerprint(root):
        if rel.endswith(".db"):
            con = sqlite3.connect(_ro_uri(os.path.join(root, rel)), uri=True)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BrainError(f"{rel} failed its integrity check; backups were kept")
            finally:
                con.close()
    removed = []
    for b in backups(data_dir):
        shutil.rmtree(b)
        removed.append(os.path.relpath(b, data_dir).replace(os.sep, "/"))
    return removed


def _ro_uri(path: str) -> str:
    import pathlib
    return pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
