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


class QuizMemoryAdapter:
    module = "quiz_memory"
    _MAP = {"game": ("verified", "observed"), "user": ("awaiting_verification", "user"),
            "solver": ("submitted", "derived"), "ai": ("submitted", "ai"),
            "search": ("submitted", "ai")}

    def __init__(self, path: str):
        self.ro = ReadOnlyDB(path)

    def search(self, query: str, limit: int = 10) -> List[Hit]:
        hits = []
        for r in self.ro.rows("SELECT id, category, text, options, answer, explanation, source, "
                              "verified, times_seen FROM questions"):
            try:
                ans = json.loads(r["answer"] or "{}")
            except ValueError:
                ans = {}
            summary = ans.get("choice") or " → ".join(ans.get("order") or []) or \
                " · ".join(f"{a} = {b}" for a, b in (ans.get("pairs") or {}).items())
            text = f"{r['text']} -> {summary}" + (f" ({r['explanation']})" if r["explanation"] else "")
            score = overlap_score(query, f"{r['text']} {summary} {r['explanation'] or ''} "
                                         f"{r['options'] or ''}")
            if score <= 0:
                continue
            status, origin = self._MAP.get(r["source"] or "", ("submitted", "user"))
            hits.append(Hit(self.module, str(r["id"]), text, status, origin, score,
                            r["category"] or "",
                            [f"quiz memory: {r['source']} answer, seen {r['times_seen']}x"],
                            {"quiz_source": r["source"], "verified_flag": bool(r["verified"])}))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def close(self) -> None:
        self.ro.close()


class AtlasAdapter:
    module = "world_atlas"

    def __init__(self, path: str):
        self.ro = ReadOnlyDB(path)

    def search(self, query: str, limit: int = 10) -> List[Hit]:
        hits: List[Hit] = []
        names = {}
        for r in self.ro.rows("SELECT room_id, name, kind, owner, aliases, status, source, note "
                              "FROM rooms"):
            names[r["room_id"]] = r["name"]
            aliases = ", ".join(json.loads(r["aliases"] or "[]"))
            text = (f"Room '{r['name']}' (ID {r['room_id']}) is a {r['kind'] or 'room'}"
                    + (f", owner {r['owner']}" if r["owner"] else "")
                    + (f"; earlier names: {aliases}" if aliases else "") + ".")
            score = overlap_score(query, f"{r['name']} {aliases} {r['kind'] or ''} {r['owner'] or ''}")
            if score <= 0:
                continue
            status = "awaiting_verification" if r["status"] == "pending" else "verified"
            hits.append(Hit(self.module, f"room:{r['room_id']}", text, status, "observed", score,
                            "lugares", [f"World Atlas: {r['source'] or 'explorer'}"],
                            {"atlas_status": r["status"], "room_id": r["room_id"]}))
        for r in self.ro.rows("SELECT marker, name, kind, room_id FROM markers"):
            if not r["name"]:
                continue
            score = overlap_score(query, r["name"])
            if score <= 0:
                continue
            text = (f"'{r['name']}' is a {r['kind'] or 'place'} on the hotel map"
                    + (f" (room ID {r['room_id']})" if r["room_id"] else "") + ".")
            hits.append(Hit(self.module, f"marker:{r['marker']}", text, "verified", "observed",
                            score, "mapa", ["World Atlas: map hover screenshot"],
                            {"marker": r["marker"]}))
        for r in self.ro.rows("SELECT from_room, to_room, method, status, times FROM connections "
                              "WHERE method='walk'"):
            a, b = names.get(r["from_room"]), names.get(r["to_room"])
            if not a or not b:
                continue
            score = overlap_score(query, f"{a} {b}")
            if score <= 0:
                continue
            hits.append(Hit(self.module, f"walk:{r['from_room']}-{r['to_room']}",
                            f"Walking from '{a}' leads to '{b}' (one way as observed).",
                            "submitted" if r["status"] == "observed" else "awaiting_verification",
                            "observed", score, "rutas",
                            [f"World Atlas: walked {r['times']}x"], {"atlas_status": r["status"]}))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def close(self) -> None:
        self.ro.close()
