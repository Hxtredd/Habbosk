"""SK Brain - read-only audit of knowledge.db (development tool, B3.5).

    BRAIN_DB_AUDIT.bat            (Windows: double-click)
    python -m puzzle_assistant.devtools.knowledge_audit [--db PATH] [--temp-dir DIR] [--report PATH]

What it does - and never does:
  * refuses to run while SK Assistant is open;
  * NEVER opens your knowledge.db with SQLite. It reads it through
    safe_snapshot.py: in memory when possible, otherwise a byte copy in a
    private, non-cloud-synced temporary folder that is deleted afterwards
    (ordinary deletion, not forensic erasure - the report says which);
  * records size, modification time and SHA-256 of knowledge.db and every
    sidecar file (-wal, -shm, -journal) and the folder listing, before and
    after, and reports whether they are exactly unchanged;
  * the report holds NO private content: no claim text, subjects, names,
    titles, URLs, notes or contributor ids - only counts, kinds, dates,
    sizes, fingerprints and the integrity result. Paths are shown with
    %APPDATA% / %USERPROFILE% instead of your user name.

The report is printed and saved as knowledge_audit_report.txt in THIS
program's folder (never in the data folder, which must stay unchanged).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from puzzle_assistant.devtools import safe_snapshot as snap  # noqa: E402

TOOL_VERSION = "B3.5-1"
REPORT_NAME = "knowledge_audit_report.txt"

# known vocabularies: anything else is reported as "other", so no free text leaks
ORIGINS = ("imported", "observed", "user", "derived", "ai")
STATUSES = ("submitted", "awaiting_verification", "verified", "conflicting", "rejected", "outdated")
SCOPES = ("personal", "shared_candidate", "shared")
CONTRIBUTOR_KINDS = ("local_user", "external_user", "moderator", "system", "importer")
SOURCE_KINDS = ("guide_page", "document", "webpage", "screenshot", "video", "game_observation",
                "user_statement", "quiz_memory", "world_atlas", "import_file", "other")
EVIDENCE_KINDS = ("direct", "confirmation", "derived", "assertion")
LINK_KINDS = ("supports", "contradicts", "derived_from", "supersedes", "duplicate_of")
BACKUP_RE = re.compile(r"^knowledge\.db\.backup-\d{8}-\d{6}(-\d+)?$")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def mask(path: str) -> str:
    """Path with the user's folders replaced by their %VARIABLE% names."""
    out = str(path)
    for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        v = os.environ.get(var)
        if v and len(v) > 3:
            out = out.replace(v, f"%{var}%")
    home = os.path.expanduser("~")
    if home and len(home) > 3:
        out = out.replace(home, "%USERPROFILE%")
    return out


def default_db() -> str:
    from puzzle_assistant.config import DATA_DIR
    return os.path.join(DATA_DIR, "knowledge.db")


def sk_is_running() -> bool:
    """Windows: SK Assistant holds a named mutex while it runs (main.py)."""
    if sys.platform != "win32":
        return False
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "PuzzleAnswerAssistant.Single")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


# -- analysis (on the snapshot connection only) -----------------------------------
def _tables(con) -> List[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def _grouped(con, table: str, col: str, known, tables) -> Dict[str, int]:
    if table not in tables:
        return {}
    out: Dict[str, int] = {}
    for value, n in con.execute(f'SELECT "{col}", COUNT(*) FROM "{table}" GROUP BY "{col}"'):
        key = value if isinstance(value, str) and value in known else "other"
        out[key] = out.get(key, 0) + n
    return out


def _count(con, table: str, tables, where: str = "") -> Optional[int]:
    if table not in tables:
        return None
    return con.execute(f'SELECT COUNT(*) FROM "{table}" {where}').fetchone()[0]


def _date(v) -> str:
    m = DATE_RE.match(v) if isinstance(v, str) else None
    return m.group(1) if m else "unknown"


def analyse(con: sqlite3.Connection) -> List[str]:
    lines: List[str] = []
    integ = con.execute("PRAGMA integrity_check").fetchone()[0]
    lines.append(f"integrity check: {'ok' if integ == 'ok' else 'PROBLEMS FOUND'}")
    tables = _tables(con)
    schema = None
    if "meta" in tables:
        r = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        schema = r[0] if r and isinstance(r[0], str) and r[0].isdigit() else None
    lines.append(f"schema version: {schema or 'unknown'}")
    if "claims" not in tables:
        lines.append("no 'claims' table: this does not look like an SK Brain database")
        return lines
    total = _count(con, "claims", tables)
    lines.append(f"claims: {total}")
    for col, known in (("origin", ORIGINS), ("status", STATUSES), ("scope", SCOPES)):
        g = _grouped(con, "claims", col, known, tables)
        lines.append(f"  by {col}: " + (", ".join(f"{k} {v}" for k, v in sorted(g.items())) or "-"))
    if total:
        lo, hi = con.execute("SELECT MIN(created), MAX(created) FROM claims").fetchone()
        lines.append(f"  created between {_date(lo)} and {_date(hi)}")
    g = _grouped(con, "contributors", "kind", CONTRIBUTOR_KINDS, tables)
    lines.append("contributors by kind: " + (", ".join(f"{k} {v}" for k, v in sorted(g.items())) or "-"))
    if g.get("external_user") or g.get("moderator"):
        lines.append("  (other people than the local user exist - e.g. test contributors)")
    g = _grouped(con, "sources", "kind", SOURCE_KINDS, tables)
    lines.append("sources by kind: " + (", ".join(f"{k} {v}" for k, v in sorted(g.items())) or "-"))
    if "sources" in tables:
        lines.append(f"  marked private: {_count(con, 'sources', tables, 'WHERE private=1')}")
    for table, col, known, label in (("evidence", "kind", EVIDENCE_KINDS, "evidence by kind"),
                                     ("claim_links", "kind", LINK_KINDS, "links by kind")):
        g = _grouped(con, table, col, known, tables)
        lines.append(f"{label}: " + (", ".join(f"{k} {v}" for k, v in sorted(g.items())) or "-"))
    for table, label in (("sections", "sections"), ("claim_versions", "earlier versions"),
                         ("claim_events", "history events"), ("claim_index", "search index rows")):
        n = _count(con, table, tables)
        lines.append(f"{label}: {'-' if n is None else n}")
    lines.append(f"full-text index (FTS5) table present: {'claim_fts' in tables}")
    return lines


# -- the audit ------------------------------------------------------------------
def _fp_lines(fp: snap.Fingerprint) -> List[str]:
    out = []
    for name, v in fp.items():
        out.append(f"  {name}: " + ("not present" if v is None else
                                    f"{v[0]} bytes, SHA-256 {v[2][:16]}..."))
    return out


def _backups(folder: str) -> List[str]:
    out, other = [], 0
    for name in snap.listing(folder):
        if name.startswith("knowledge.db.backup"):
            p = os.path.join(folder, name)
            if BACKUP_RE.match(name) and os.path.isfile(p):
                out.append(f"  {name} ({os.path.getsize(p)} bytes, not opened)")
            else:
                other += 1
    if other:
        out.append(f"  {other} other backup-like file(s), not opened")
    return out


def audit(db_path: str, temp_base: Optional[str] = None,
          running=sk_is_running) -> Tuple[int, str]:
    """(exit code, report text). Exit 0 = audited and originals unchanged."""
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = ["SK Brain - knowledge.db audit (read-only)", "=" * 60,
            f"tool {TOOL_VERSION}, {now}", f"database: {mask(db_path)}"]
    if running():
        return 1, "\n".join(head + ["", "SK Assistant is running. Close it and run the audit again.",
                                    "Nothing was read or changed."])
    folder = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isfile(db_path):
        return 0, "\n".join(head + ["", "No knowledge.db found - nothing to audit. "
                                        "Nothing was read or changed."])
    list_before = snap.listing(folder)
    fp_before = snap.fingerprint(db_path)
    body: List[str] = ["", "Files before:"] + _fp_lines(fp_before)
    s = None
    code = 0
    try:
        try:
            s = snap.open_snapshot(db_path, temp_base)
        except snap.SnapshotRefused as exc:
            body += ["", f"Not audited: {exc}."]
            code = 1
        except sqlite3.DatabaseError:
            body += ["", "Not audited: the file is not a readable SQLite database."]
            code = 1
        if s is not None:
            body += ["", f"Read method: {s.method}"]
            if s.method == "disk copy":
                body.append(f"  temporary folder: {mask(s.temp_dir)} (local, not cloud-synced)")
            if s.leftovers_removed:
                body.append(f"  removed {s.leftovers_removed} leftover temporary copy/copies "
                            "from an earlier interrupted run")
            body += [f"  note: {n}" for n in s.notes]
            body += ["", "Contents (counts only - no text, names, titles or ids):"]
            try:
                body += ["  " + ln for ln in analyse(s.con)]
            except sqlite3.DatabaseError:
                body += ["  the database could not be read completely (damaged?)"]
                code = 1
    finally:
        if s is not None:
            s.close()
            if s.method == "disk copy":
                body.append("")
                body.append("Temporary copy: " + ("deleted (ordinary file deletion - NOT forensic "
                                                  "erasure: the data may remain on the disk until "
                                                  "it is overwritten)" if s.temp_removed else
                                                  f"COULD NOT BE DELETED - please delete "
                                                  f"{mask(s.temp_dir)} yourself"))
                if not s.temp_removed:
                    code = 1
            else:
                body += ["", "Temporary copy: none was written to disk (read in memory)."]
    fp_after = snap.fingerprint(db_path)
    list_after = snap.listing(folder)
    same = fp_after == fp_before
    same_list = list_after == list_before
    body += ["", "Original files after:"] + _fp_lines(fp_after)
    body.append(f"  knowledge.db and sidecar files exactly unchanged (size, time, SHA-256): "
                f"{'YES' if same else 'NO - see above'}")
    body.append(f"  data folder: {len(list_after)} files, none created or removed: "
                f"{'YES' if same_list else 'NO'}")
    backups = _backups(folder)
    body += ["", "Bench backups in the data folder:"] + (backups or ["  none"])
    if not (same and same_list):
        code = 2
    body += ["", "=" * 60, "RESULT: " + {0: "audited; originals unchanged",
                                        1: "not completed (see above); originals unchanged"
                                        if same and same_list else "",
                                        2: "ORIGINAL FILES CHANGED DURING THE AUDIT - "
                                           "was SK Assistant or another program running?"}[code]]
    return code, "\n".join(head + body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only audit of the SK Brain database.")
    ap.add_argument("--db", default=None, help="database file (default: your knowledge.db)")
    ap.add_argument("--temp-dir", default=None, help="local, non-synced folder for a temporary copy")
    ap.add_argument("--report", default=os.path.join(HERE, REPORT_NAME))
    a = ap.parse_args(argv)
    db = a.db or default_db()
    if os.path.dirname(os.path.abspath(a.report)) == os.path.dirname(os.path.abspath(db)):
        print("The report must not be saved in the data folder.", file=sys.stderr)
        return 1
    code, text = audit(db, a.temp_dir)
    print(text)
    try:
        with open(a.report, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\nSaved: {mask(a.report)}")
    except OSError as exc:
        print(f"(could not save the report: {exc})")
    return code


if __name__ == "__main__":
    sys.exit(main())
