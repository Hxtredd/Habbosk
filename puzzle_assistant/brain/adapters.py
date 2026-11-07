"""Read-only bridges from the SK Brain to existing knowledge.

The quiz memory (habbosk.db) and the World Atlas (atlas.db) keep working
exactly as before; the Brain never migrates, rewrites or modifies them. The
adapters open those files with SQLite's read-only mode (`mode=ro`) - a write
would fail - and they never create a missing file.

Status mapping (each module's own trust model -> Brain vocabulary). It is
shown with every result so the original meaning is never hidden:
  quiz memory   game-revealed answer   -> verified  / observed
                user correction        -> awaiting_verification / user
                built-in solver        -> submitted / derived
                AI suggestion          -> submitted / ai
  World Atlas   room accepted / calibrated   -> verified / observed
                room waiting for approval    -> awaiting_verification / observed
                walking route seen by SK     -> submitted / observed
                map place (hover screenshot) -> verified / observed
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from typing import Iterable, List, Optional

from .model import Hit


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9ñ ]+", " ", s)


_STOP = set("""el la los las un una unos unas de del al y o u en a que es son por para con
sin se su sus lo le les mi tu como cual cuales quien que donde cuando the of and is to
esta este estos estas ese esa eso hay muy mas pero si no""".split())


def words(s: str) -> set:
    return {w for w in _norm(s).split() if len(w) >= 3 and w not in _STOP}


def overlap_score(query: str, text: str) -> float:
    """Basic relevance for B1 (B2 replaces it with ranked full-text search):
    share of the query's words found in the text."""
    q = words(query)
    if not q:
        return 0.0
    return len(q & words(text)) / len(q)


class ReadOnlyDB:
    """A SQLite file opened read-only, or nothing if it doesn't exist."""

    def __init__(self, path: str):
        self.path = path
        self.db: Optional[sqlite3.Connection] = None
        if path and os.path.exists(path):
            uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
            self.db = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self.db.row_factory = sqlite3.Row

    def rows(self, sql: str, args: Iterable = ()) -> List[sqlite3.Row]:
        if self.db is None:
            return []
        try:
            return self.db.execute(sql, tuple(args)).fetchall()
        except sqlite3.Error:
            return []            # older file without that table: nothing from it

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None


def _search_records(records: List[dict], query: str, limit: int) -> List[Hit]:
    """B1 behaviour (kept for older callers): word overlap over records()."""
    hits = []
    for r in records:
        score = overlap_score(query, r["search_text"])
        if score > 0:
            hits.append(Hit(r["module"], r["record_id"], r["text"], r["status"], r["origin"],
                            score, r["category"], r["sources"], r["detail"]))
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]


class QuizMemoryAdapter:
    module = "quiz_memory"
    _MAP = {"game": ("verified", "observed"), "user": ("awaiting_verification", "user"),
            "solver": ("submitted", "derived"), "ai": ("submitted", "ai"),
            "search": ("submitted", "ai")}

    def __init__(self, path: str):
        self.ro = ReadOnlyDB(path)

    def records(self) -> List[dict]:
        """Every learned question as a searchable record (read-only)."""
        out = []
        for r in self.ro.rows("SELECT id, category, text, options, answer, explanation, source, "
                              "verified, times_seen FROM questions"):
            try:
                ans = json.loads(r["answer"] or "{}")
            except ValueError:
                ans = {}
            summary = ans.get("choice") or " → ".join(ans.get("order") or []) or \
                " · ".join(f"{a} = {b}" for a, b in (ans.get("pairs") or {}).items())
            status, origin = self._MAP.get(r["source"] or "", ("submitted", "user"))
            out.append({
                "module": self.module, "record_id": str(r["id"]),
                "text": f"{r['text']} -> {summary}" + (f" ({r['explanation']})"
                                                       if r["explanation"] else ""),
                "search_text": f"{r['text']} {summary} {r['explanation'] or ''} {r['options'] or ''}",
                "subject": "", "status": status, "origin": origin,
                "category": r["category"] or "",
                "sources": [f"quiz memory: {r['source']} answer, seen {r['times_seen']}x"],
                "detail": {"quiz_source": r["source"], "verified_flag": bool(r["verified"])}})
        return out

    def entity_names(self) -> List[str]:
        return []

    def search(self, query: str, limit: int = 10) -> List[Hit]:
        return _search_records(self.records(), query, limit)

    def close(self) -> None:
        self.ro.close()


class AtlasAdapter:
    module = "world_atlas"

    def __init__(self, path: str):
        self.ro = ReadOnlyDB(path)

    def records(self) -> List[dict]:
        out: List[dict] = []
        names = {}
        for r in self.ro.rows("SELECT room_id, name, kind, owner, aliases, status, source, note "
                              "FROM rooms"):
            names[r["room_id"]] = r["name"]
            aliases = ", ".join(json.loads(r["aliases"] or "[]"))
            out.append({
                "module": self.module, "record_id": f"room:{r['room_id']}",
                "text": (f"Room '{r['name']}' (ID {r['room_id']}) is a {r['kind'] or 'room'}"
                         + (f", owner {r['owner']}" if r["owner"] else "")
                         + (f"; earlier names: {aliases}" if aliases else "") + "."),
                "search_text": f"{r['name']} {aliases} {r['kind'] or ''} {r['owner'] or ''} "
                               f"room id {r['room_id']}",
                "subject": r["name"],
                "status": "awaiting_verification" if r["status"] == "pending" else "verified",
                "origin": "observed", "category": "lugares",
                "sources": [f"World Atlas: {r['source'] or 'explorer'}"],
                "detail": {"atlas_status": r["status"], "room_id": r["room_id"]}})
        for r in self.ro.rows("SELECT marker, name, kind, room_id FROM markers"):
            if not r["name"]:
                continue
            out.append({
                "module": self.module, "record_id": f"marker:{r['marker']}",
                "text": (f"'{r['name']}' is a {r['kind'] or 'place'} on the hotel map"
                         + (f" (room ID {r['room_id']})" if r["room_id"] else "") + "."),
                "search_text": r["name"], "subject": r["name"], "status": "verified",
                "origin": "observed", "category": "mapa",
                "sources": ["World Atlas: map hover screenshot"], "detail": {"marker": r["marker"]}})
        for r in self.ro.rows("SELECT from_room, to_room, method, status, times FROM connections "
                              "WHERE method='walk'"):
            a, b = names.get(r["from_room"]), names.get(r["to_room"])
            if not a or not b:
                continue
            out.append({
                "module": self.module, "record_id": f"walk:{r['from_room']}-{r['to_room']}",
                "text": f"Walking from '{a}' leads to '{b}' (one way as observed).",
                "search_text": f"{a} {b} walk lead route", "subject": b,
                "status": "submitted" if r["status"] == "observed" else "awaiting_verification",
                "origin": "observed", "category": "rutas",
                "sources": [f"World Atlas: walked {r['times']}x"],
                "detail": {"atlas_status": r["status"]}})
        return out

    def entity_names(self) -> List[str]:
        names = [r["name"] for r in self.ro.rows("SELECT name FROM rooms")]
        names += [r["name"] for r in self.ro.rows("SELECT name FROM markers WHERE name IS NOT NULL")]
        return [n for n in names if n]

    def search(self, query: str, limit: int = 10) -> List[Hit]:
        out = []
        for r in self.records():
            # B1 matched rooms on name/aliases/kind/owner only (not 'room id')
            st = r["search_text"].rsplit(" room id ", 1)[0] if r["record_id"].startswith("room:") \
                else (r["search_text"].replace(" walk lead route", "")
                      if r["record_id"].startswith("walk:") else r["search_text"])
            out.append(dict(r, search_text=st))
        return _search_records(out, query, limit)

    def close(self) -> None:
        self.ro.close()
