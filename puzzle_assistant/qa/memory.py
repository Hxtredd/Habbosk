"""Local memory of learned questions (SQLite, data/habbosk.db).

* Survives restarts and works offline.
* Finds a question again when the options are shuffled, and when it is
  slightly reworded, but never when a negation changes the meaning
  ("¿cuál es...?" vs "¿cuál NO es...?").
* A weaker source never overwrites a stronger one: what the game itself
  showed as correct beats a user correction, which beats the built-in
  solvers, which beat an AI guess. Blocked overwrites are logged.
* Keeps a history of every change and correction, and API usage per month.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..config import DATA_DIR
from .model import CHOICE, ORDER, PAIRS, SOURCE_RANK, Answer, Question
from .text import normalize

DB_PATH = os.path.join(DATA_DIR, "habbosk.db")

NEGATIONS = {"no", "not", "nunca", "never", "falso", "falsa", "false", "incorrecto",
             "incorrecta", "incorrect", "error", "errores", "excepto", "except", "menos",
             "least", "wrong", "mal"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    norm_text TEXT NOT NULL,
    options_key TEXT NOT NULL,
    category TEXT, kind TEXT, text TEXT, clues TEXT, options TEXT, right_opts TEXT,
    language TEXT, answer TEXT, explanation TEXT, source TEXT,
    verified INTEGER DEFAULT 0, confidence REAL DEFAULT 0,
    times_seen INTEGER DEFAULT 0, times_correct INTEGER DEFAULT 0,
    times_wrong INTEGER DEFAULT 0, created TEXT, updated TEXT
);
CREATE INDEX IF NOT EXISTS idx_q_text ON questions(norm_text);
CREATE INDEX IF NOT EXISTS idx_q_opts ON questions(options_key);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, question_id INTEGER, event TEXT, old TEXT, new TEXT,
    source TEXT, detail TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    month TEXT, provider TEXT, requests INTEGER DEFAULT 0, est_cost REAL DEFAULT 0,
    PRIMARY KEY (month, provider)
);
"""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def options_key(q: Question) -> str:
    opts = sorted(normalize(o) for o in q.options)
    right = sorted(normalize(o) for o in q.right)
    return "|".join(opts) + ("||" + "|".join(right) if right else "")


def question_key(q: Question) -> str:
    # hint bullets are NOT part of the key: they appear only after asking for a hint
    return normalize(q.category) + "#" + normalize(q.text) + "#" + options_key(q)


def _negations(norm_text: str) -> set:
    return set(norm_text.split()) & NEGATIONS


@dataclass
class Match:
    answer: Answer      # fitted to the options on screen (for the on-screen marks)
    how: str            # "exact" | "reordered" | "similar"
    similarity: float
    question_id: int
    stored: Optional[Answer] = None   # the answer as learned (clean text, for display)


class Memory:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- lookup ------------------------------------------------------------
    def lookup(self, q: Question) -> Optional[Match]:
        key = question_key(q)
        nt = normalize(q.text)
        ok = options_key(q)
        with self._lock:
            row = self._db.execute("SELECT * FROM questions WHERE key=?", (key,)).fetchone()
            if row is not None:
                return self._match(row, q, "exact", 1.0)
            # same options (any order), reworded question
            rows = self._db.execute("SELECT * FROM questions WHERE options_key=? AND kind=?",
                                    (ok, q.kind)).fetchall()
            best = self._best_similar(rows, nt, 0.80)
            if best is not None:
                return self._match(best[0], q, "reordered" if best[1] >= 0.999 else "similar",
                                   best[1])
            # same question, partly different options (the game swaps distractors)
            rows = self._db.execute("SELECT * FROM questions WHERE norm_text=? AND kind=?",
                                    (nt, q.kind)).fetchall()
            best = self._best_similar(rows, nt, 0.999)
            if best is not None:
                m = self._match(best[0], q, "similar", best[1])
                if m is not None:
                    return m
            # small reading differences in the question AND the options
            rows = self._db.execute("SELECT * FROM questions WHERE kind=? AND category=?",
                                    (q.kind, q.category)).fetchall()
            neg = _negations(nt)
            scored = []
            for row in rows:
                if _negations(row["norm_text"]) != neg:
                    continue
                r = difflib.SequenceMatcher(None, nt, row["norm_text"]).ratio()
                if r >= 0.9:
                    scored.append((r, row))
            for r, row in sorted(scored, key=lambda t: -t[0]):
                m = self._match(row, q, "similar", r)
                if m is not None:
                    return m
        return None

    def _best_similar(self, rows, nt: str, threshold: float) -> Optional[Tuple[sqlite3.Row, float]]:
        best, best_r = None, 0.0
        neg = _negations(nt)
        for row in rows:
            if _negations(row["norm_text"]) != neg:
                continue  # a negation changes the meaning: never reuse
            r = difflib.SequenceMatcher(None, nt, row["norm_text"]).ratio()
            if r >= threshold and r > best_r:
                best, best_r = row, r
        return (best, best_r) if best is not None else None

    def _match(self, row, q: Question, how: str, sim: float) -> Optional[Match]:
        stored = Answer.from_dict(json.loads(row["answer"]))
        ans = _fit_to_options(Answer.from_dict(json.loads(row["answer"])), q)
        if ans is None:
            return None
        return Match(ans, how, sim, row["id"], stored)

    # -- learning ----------------------------------------------------------
    def save(self, q: Question, ans: Answer, force: bool = False) -> str:
        """Store an answer. Returns 'new', 'updated', 'same' or 'blocked'.
        force=True: an explicit user correction (always applied, logged)."""
        key = question_key(q)
        now = _now()
        with self._lock:
            row = self._db.execute("SELECT * FROM questions WHERE key=?", (key,)).fetchone()
            if row is None:
                cur = self._db.execute(
                    "INSERT INTO questions (key, norm_text, options_key, category, kind, text, "
                    "clues, options, right_opts, language, answer, explanation, source, verified, "
                    "confidence, times_seen, created, updated) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                    (key, normalize(q.text), options_key(q), q.category, q.kind, q.text,
                     json.dumps(q.clues, ensure_ascii=False), json.dumps(q.options, ensure_ascii=False),
                     json.dumps(q.right, ensure_ascii=False), q.language,
                     json.dumps(ans.to_dict(), ensure_ascii=False), ans.explanation, ans.source,
                     int(ans.verified), ans.confidence, now, now))
                self._log(cur.lastrowid, "learned", None, ans, ans.source, "")
                self._db.commit()
                return "new"
            old = Answer.from_dict(json.loads(row["answer"]))
            same = old.summary() == ans.summary()
            stronger_old = SOURCE_RANK.get(old.source, 0) > SOURCE_RANK.get(ans.source, 0)
            if same:
                if ans.explanation and not row["explanation"]:
                    self._db.execute("UPDATE questions SET explanation=? WHERE id=?",
                                     (ans.explanation, row["id"]))
                if SOURCE_RANK.get(ans.source, 0) > SOURCE_RANK.get(old.source, 0):
                    self._write(row["id"], ans, now)       # same answer, better source
                self._db.commit()
                return "same"
            if (old.verified and stronger_old and not force) or (stronger_old and not force):
                self._log(row["id"], "overwrite_blocked", old, ans, ans.source,
                          f"kept the {old.source} answer")
                self._db.commit()
                return "blocked"
            self._write(row["id"], ans, now)
            self._log(row["id"], "correction" if force or ans.source == "user" else "updated",
                      old, ans, ans.source, "")
            self._db.commit()
            return "updated"

    def _write(self, qid: int, ans: Answer, now: str) -> None:
        self._db.execute(
            "UPDATE questions SET answer=?, explanation=?, source=?, verified=?, confidence=?, "
            "updated=? WHERE id=?",
            (json.dumps(ans.to_dict(), ensure_ascii=False), ans.explanation, ans.source,
             int(ans.verified), ans.confidence, now, qid))

    def correct(self, q: Question, ans: Answer) -> str:
        """Manual correction by the user: always applied and logged."""
        ans.source, ans.verified = "user", True
        return self.save(q, ans, force=True)

    def record_feedback(self, q: Question, picked: Optional[str], was_correct: Optional[bool]) -> None:
        """What the player picked and what the game said (when visible)."""
        key = question_key(q)
        with self._lock:
            row = self._db.execute("SELECT id FROM questions WHERE key=?", (key,)).fetchone()
            if row is None:
                return
            col = {True: "times_correct", False: "times_wrong"}.get(was_correct)
            if col:
                self._db.execute(f"UPDATE questions SET {col}={col}+1 WHERE id=?", (row["id"],))
            self._db.execute("UPDATE questions SET times_seen=times_seen+1 WHERE id=?", (row["id"],))
            self._log(row["id"], "feedback", None, None, "game",
                      json.dumps({"picked": picked, "correct": was_correct}, ensure_ascii=False))
            self._db.commit()

    def _log(self, qid, event, old: Optional[Answer], new: Optional[Answer], source, detail) -> None:
        self._db.execute(
            "INSERT INTO history (question_id, event, old, new, source, detail, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (qid, event, json.dumps(old.to_dict(), ensure_ascii=False) if old else None,
             json.dumps(new.to_dict(), ensure_ascii=False) if new else None, source, detail, _now()))

    # -- usage / cost ------------------------------------------------------
    def add_usage(self, provider: str, requests: int = 1, cost: float = 0.0) -> None:
        month = _dt.date.today().strftime("%Y-%m")
        with self._lock:
            self._db.execute(
                "INSERT INTO usage (month, provider, requests, est_cost) VALUES (?,?,?,?) "
                "ON CONFLICT(month, provider) DO UPDATE SET requests=requests+?, est_cost=est_cost+?",
                (month, provider, requests, cost, requests, cost))
            self._db.commit()

    def month_usage(self) -> Tuple[int, float]:
        month = _dt.date.today().strftime("%Y-%m")
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(requests),0), COALESCE(SUM(est_cost),0) "
                                   "FROM usage WHERE month=?", (month,)).fetchone()
        return int(row[0]), float(row[1])

    # -- management ----------------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            v = self._db.execute("SELECT COUNT(*) FROM questions WHERE verified=1").fetchone()[0]
            c = self._db.execute("SELECT COUNT(*) FROM history WHERE event='correction'").fetchone()[0]
        return {"questions": n, "verified": v, "corrections": c}

    def recent(self, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM questions ORDER BY updated DESC LIMIT ?",
                                    (limit,)).fetchall()
        return [_row_dict(r) for r in rows]

    def export_json(self, path: str) -> int:
        with self._lock:
            rows = [_row_dict(r) for r in self._db.execute("SELECT * FROM questions ORDER BY id")]
            hist = [dict(r) for r in self._db.execute("SELECT * FROM history ORDER BY id")]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"questions": rows, "history": hist}, fh, ensure_ascii=False, indent=2)
        return len(rows)

    def clear(self) -> None:
        """Forget everything that was learned (questions, history, usage)."""
        with self._lock:
            self._db.executescript("DELETE FROM questions; DELETE FROM history; DELETE FROM usage;")
            self._db.commit()


def _row_dict(r) -> dict:
    d = dict(r)
    for k in ("clues", "options", "right_opts", "answer"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def _strip_tail(n: str) -> str:
    words = n.split()
    while len(words) > 1 and len(words[-1]) <= 2 and words[-1].isalpha():
        words.pop()
    return " ".join(words)


def _find_option(text: str, options: List[str]) -> Optional[str]:
    """The on-screen option matching a stored one. Exact after normalising;
    long options may differ by a reading slip ('banco.' / 'banc:'). Short
    options ('63-8', 'V') must match exactly: one character changes them."""
    n = normalize(text)
    by_norm = {normalize(o): o for o in options}
    if n in by_norm:
        return by_norm[n]
    # a stray 1-2 letter fragment after the text (icon, pointer) on either side
    core = _strip_tail(n)
    hits = [o for k, o in by_norm.items() if _strip_tail(k) == core and len(core) >= 3]
    if len(hits) == 1:
        return hits[0]
    if len(n) < 8:
        return None
    best, best_r = None, 0.0
    for k, o in by_norm.items():
        r = difflib.SequenceMatcher(None, n, k).ratio()
        if r > best_r:
            best, best_r = o, r
    # must be clearly closer than any other option
    others = sorted((difflib.SequenceMatcher(None, n, k).ratio() for k in by_norm), reverse=True)
    if best_r >= 0.88 and (len(others) < 2 or others[0] - others[1] >= 0.1):
        return best
    return None


def _fit_to_options(ans: Answer, q: Question) -> Optional[Answer]:
    """Map a stored answer onto the options currently on screen (they may be
    shuffled, partly different, or read with small slips). None if it no
    longer fits."""
    if ans.kind == CHOICE:
        hit = _find_option(ans.choice or "", q.options)
        if hit is None:
            return None
        ans.choice = hit
    elif ans.kind == ORDER:
        items = [_find_option(x, q.options) for x in ans.order]
        if None in items or len(set(items)) != len(q.options):
            return None
        ans.order = items
    elif ans.kind == PAIRS:
        pairs = {}
        for a, b in ans.pairs.items():
            la, rb = _find_option(a, q.options), _find_option(b, q.right)
            if la is None or rb is None:
                return None
            pairs[la] = rb
        if len(pairs) != len(q.options):
            return None
        ans.pairs = pairs
    return ans
