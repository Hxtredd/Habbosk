"""World Atlas database (SQLite, in the user's data folder next to the quiz
memory but separate from it).

Rules agreed with the owner:
  * Habbo's own room IDs are the permanent keys; never invented.
  * Names are not unique; a renamed room keeps its old names as aliases.
  * A room can be known without knowing how to reach it.
  * Connections are never assumed to work both ways; walking ('walk') and
    taxi trips ('taxi') are stored separately.
  * New rooms wait for the owner's Accept / Edit; corrections keep the
    original reading (audit trail).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
from typing import Dict, List, Optional

from ..config import DATA_DIR, app_dir

DB_PATH = os.path.join(DATA_DIR, "atlas.db")
INPUT_DIR = os.path.join(app_dir(), "atlas_input")
IMPORT_TAG = "import_atlas_input_v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    room_id INTEGER PRIMARY KEY, name TEXT, kind TEXT, owner TEXT,
    aliases TEXT DEFAULT '[]', status TEXT, source TEXT, note TEXT,
    first_seen TEXT, last_visited TEXT, visits INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending (
    id INTEGER PRIMARY KEY, room_id INTEGER, name TEXT, owner TEXT,
    from_room INTEGER, method TEXT, detected_at TEXT
);
CREATE TABLE IF NOT EXISTS markers (
    marker TEXT PRIMARY KEY, name TEXT, kind TEXT, x INTEGER, y INTEGER, room_id INTEGER
);
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY, from_room INTEGER, to_room INTEGER, method TEXT,
    status TEXT, times INTEGER DEFAULT 0, first_seen TEXT, last_seen TEXT, evidence TEXT,
    UNIQUE (from_room, to_room, method)
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY, room_id INTEGER, name TEXT, from_room INTEGER,
    method TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY, room_id INTEGER, detected TEXT, corrected TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def guess_kind(room_id: int, name: str, owner: Optional[str]) -> str:
    n = (name or "").lower()
    if room_id >= 1_000_000_000:
        return "generated"                       # e.g. mine levels
    if n.startswith("avenida") or n.startswith("ct."):
        return "street"
    if owner and owner.lower() not in ("habbosk", "sk"):
        return "player"
    return "place"


class Atlas:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- meta ----------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
            self._db.commit()

    # -- rooms ---------------------------------------------------------------
    def room(self, room_id: int) -> Optional[dict]:
        with self._lock:
            r = self._db.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        return _room(r) if r else None

    def rooms(self, search: str = "") -> List[dict]:
        q = f"%{search.lower()}%"
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM rooms WHERE lower(name) LIKE ? OR CAST(room_id AS TEXT) LIKE ? "
                "OR lower(aliases) LIKE ? ORDER BY room_id", (q, q, q)).fetchall()
        return [_room(r) for r in rows]

    def by_name(self, name: str) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM rooms WHERE lower(name)=?",
                                    ((name or "").lower(),)).fetchall()
        return [_room(r) for r in rows]

    def add_room(self, room_id: int, name: str, owner: Optional[str] = None,
                 status: str = "known", source: str = "", note: str = "",
                 kind: Optional[str] = None) -> str:
        """Insert or update. Returns 'new', 'renamed' or 'same'."""
        with self._lock:
            old = self.room(room_id)
            if old is None:
                self._db.execute(
                    "INSERT INTO rooms (room_id, name, kind, owner, status, source, note, first_seen) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (room_id, name, kind or guess_kind(room_id, name, owner), owner, status,
                     source, note, _now()))
                self._db.commit()
                return "new"
            out = "same"
            if name and old["name"] and name != old["name"]:
                aliases = old["aliases"]
                if old["name"] not in aliases:
                    aliases.append(old["name"])
                self._db.execute("UPDATE rooms SET name=?, aliases=? WHERE room_id=?",
                                 (name, json.dumps(aliases, ensure_ascii=False), room_id))
                out = "renamed"
            if owner and not old["owner"]:
                self._db.execute("UPDATE rooms SET owner=? WHERE room_id=?", (owner, room_id))
            self._db.commit()
            return out

    # -- what the explorer saw ----------------------------------------------
    def record_visit(self, room_id: int, name: str, owner: Optional[str],
                     from_room: Optional[int], method: str) -> dict:
        """A room change seen on screen. Known rooms are updated straight
        away; a new room waits for approval. Returns what happened."""
        now = _now()
        res = {"room_id": room_id, "new_room": False, "renamed": False, "connection": None}
        with self._lock:
            known = self.room(room_id)
            if known is None or known["status"] == "pending":
                if known is None:
                    self.add_room(room_id, name, owner, status="pending", source="explorer")
                    self._db.execute(
                        "INSERT INTO pending (room_id, name, owner, from_room, method, detected_at) "
                        "VALUES (?,?,?,?,?,?)", (room_id, name, owner, from_room, method, now))
                res["new_room"] = True
            else:
                res["renamed"] = self.add_room(room_id, name, owner) == "renamed"
            self._db.execute("UPDATE rooms SET last_visited=?, visits=visits+1 WHERE room_id=?",
                             (now, room_id))
            self._db.execute("INSERT INTO visits (room_id, name, from_room, method, at) "
                             "VALUES (?,?,?,?,?)", (room_id, name, from_room, method, now))
            # only walking is a route between rooms (a taxi goes anywhere)
            if from_room is not None and from_room != room_id and method == "walk":
                status = "awaiting approval" if res["new_room"] else "observed"
                self._db.execute(
                    "INSERT INTO connections (from_room, to_room, method, status, times, first_seen, "
                    "last_seen, evidence) VALUES (?,?,?,?,1,?,?,'explorer') "
                    "ON CONFLICT(from_room, to_room, method) DO UPDATE SET times=times+1, last_seen=?",
                    (from_room, room_id, method, status, now, now, now))
                res["connection"] = (from_room, room_id, method)
            self._db.commit()
        return res

    def pending(self) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM pending ORDER BY id")]

    def accept(self, pending_id: int, room_id: Optional[int] = None,
               name: Optional[str] = None) -> str:
        """Accept a detected room, optionally with corrected ID / name.
        Returns 'ok' or 'duplicate' (the corrected ID belongs to another room)."""
        with self._lock:
            p = self._db.execute("SELECT * FROM pending WHERE id=?", (pending_id,)).fetchone()
            if p is None:
                return "gone"
            rid = room_id if room_id is not None else p["room_id"]
            nm = (name or p["name"] or "").strip()
            if rid != p["room_id"]:
                other = self.room(rid)
                if other is not None and other["status"] != "pending":
                    return "duplicate"
                self._db.execute("DELETE FROM rooms WHERE room_id=? AND status='pending'",
                                 (p["room_id"],))
                for col in ("from_room", "to_room"):
                    self._db.execute(f"UPDATE OR IGNORE connections SET {col}=? WHERE {col}=?",
                                     (rid, p["room_id"]))
                self._db.execute("UPDATE visits SET room_id=? WHERE room_id=?", (rid, p["room_id"]))
            if rid != p["room_id"] or nm != (p["name"] or ""):
                self._db.execute(
                    "INSERT INTO corrections (room_id, detected, corrected, at) VALUES (?,?,?,?)",
                    (rid, json.dumps({"room_id": p["room_id"], "name": p["name"]}, ensure_ascii=False),
                     json.dumps({"room_id": rid, "name": nm}, ensure_ascii=False), _now()))
            if self.room(rid) is None:
                self.add_room(rid, nm, p["owner"], status="visited", source="explorer (accepted)")
            self._db.execute("UPDATE rooms SET name=?, status='visited', source=? WHERE room_id=?",
                             (nm, "explorer (corrected)" if (rid != p["room_id"] or nm != p["name"])
                              else "explorer (accepted)", rid))
            self._db.execute("UPDATE connections SET status='observed' "
                             "WHERE to_room=? AND status='awaiting approval'", (rid,))
            self._db.execute("DELETE FROM pending WHERE id=?", (pending_id,))
            self._db.commit()
            return "ok"

    def decline(self, pending_id: int) -> None:
        """Not this room: forget the detection (its connection stays unverified)."""
        with self._lock:
            p = self._db.execute("SELECT * FROM pending WHERE id=?", (pending_id,)).fetchone()
            if p is None:
                return
            self._db.execute("DELETE FROM rooms WHERE room_id=? AND status='pending'", (p["room_id"],))
            self._db.execute("UPDATE connections SET status='unverified' "
                             "WHERE to_room=? AND status='awaiting approval'", (p["room_id"],))
            self._db.execute("DELETE FROM pending WHERE id=?", (pending_id,))
            self._db.commit()

    # -- connections, markers, stats ----------------------------------------
    def connections(self, room_id: Optional[int] = None) -> List[dict]:
        with self._lock:
            if room_id is None:
                rows = self._db.execute("SELECT * FROM connections ORDER BY id").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM connections WHERE from_room=? OR to_room=? "
                                        "ORDER BY id", (room_id, room_id)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        with self._lock:
            one = lambda q: self._db.execute(q).fetchone()[0]  # noqa: E731
            return {"rooms": one("SELECT COUNT(*) FROM rooms WHERE status!='pending'"),
                    "visited": one("SELECT COUNT(*) FROM rooms WHERE visits>0 OR status='visited'"),
                    "pending": one("SELECT COUNT(*) FROM pending"),
                    "walk": one("SELECT COUNT(*) FROM connections WHERE method='walk' "
                                "AND status='observed'"),
                    "taxi": one("SELECT COUNT(*) FROM connections WHERE method='taxi' "
                                "AND status='observed'"),
                    "markers": one("SELECT COUNT(*) FROM markers"),
                    "markers_linked": one("SELECT COUNT(*) FROM markers WHERE room_id IS NOT NULL")}

    def export(self, path: str) -> dict:
        """Everything, as JSON - for the owner to send back (world knowledge)."""
        with self._lock:
            data = {t: [dict(r) for r in self._db.execute(f"SELECT * FROM {t}")]
                    for t in ("rooms", "pending", "markers", "connections", "visits", "corrections")}
        for r in data["rooms"]:
            r["aliases"] = json.loads(r.get("aliases") or "[]")
        data["exported"] = _now()
        data["about"] = "SK Assistant World Atlas export (habbosk.us)"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        return {k: len(v) for k, v in data.items() if isinstance(v, list)}

    # -- first start: what was collected during calibration -----------------
    def import_input(self, folder: str = INPUT_DIR) -> Optional[dict]:
        if self.get_meta(IMPORT_TAG) or not os.path.isdir(folder):
            return None
        counts = {"rooms": 0, "markers": 0, "connections": 0}
        rp = os.path.join(folder, "rooms.json")
        if os.path.exists(rp):
            with open(rp, encoding="utf-8") as fh:
                for r in json.load(fh).get("rooms", []):
                    note = r.get("note", "")
                    owner = None
                    if "owner " in note:
                        owner = note.split("owner ", 1)[1].split()[0].strip(";,()")
                    self.add_room(int(r["room_id"]), r["name"], owner, status="known",
                                  source="calibration screenshots", note=note)
                    counts["rooms"] += 1
        mp = os.path.join(folder, "map", "map_markers.json")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as fh:
                for m in json.load(fh).get("markers", []):
                    with self._lock:
                        self._db.execute("INSERT OR REPLACE INTO markers VALUES (?,?,?,?,?,?)",
                                         (m["marker"], m.get("name"), m.get("kind"), m.get("x"),
                                          m.get("y"), m.get("room_id")))
                    counts["markers"] += 1
        op = os.path.join(folder, "observed.json")
        if os.path.exists(op):
            with open(op, encoding="utf-8") as fh:
                for c in json.load(fh).get("connections", []):
                    if not isinstance(c.get("to_room"), int):
                        continue
                    method = "taxi" if "taxi" in c.get("method", "") else "walk"
                    with self._lock:
                        self._db.execute(
                            "INSERT OR IGNORE INTO connections (from_room, to_room, method, status, "
                            "times, first_seen, last_seen, evidence) VALUES (?,?,?,'observed',1,?,?,?)",
                            (c["from_room"], c["to_room"], method, _now(), _now(), c.get("evidence")))
                    counts["connections"] += 1
        with self._lock:
            self._db.commit()
        self.set_meta(IMPORT_TAG, _now())
        return counts


def _room(r) -> dict:
    d = dict(r)
    d["aliases"] = json.loads(d.get("aliases") or "[]")
    return d
