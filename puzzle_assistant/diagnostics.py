"""Local, private timing log for the AI (owner-approved change A).

One line of JSON per AI request in %APPDATA%\\SK Assistant\\diagnostics\\ai_timing.jsonl.
Only timings, outcomes and error TYPES are written - never question text,
options, answers, explanations, API keys or provider error messages. Every
field is checked against a fixed list of names, types and allowed values, so
nothing else can slip in.

Size-capped: when the file reaches MAX_BYTES it becomes ai_timing.1.jsonl
(the previous .1 file is deleted), so the folder never holds more than about
2 x MAX_BYTES. Writing never raises: a full disk or a locked file only means
that line is not logged.

The engine logs only when the UI gives it a log (Engine.diag, like memory
and ai), so tests and tools never write into the user's real data folder.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
from typing import Optional

MAX_BYTES = 256 * 1024
FILE_NAME = "ai_timing.jsonl"

PROVIDERS = {"gemini", "anthropic", "openai", ""}
OUTCOMES = {
    "shown",          # the answer was shown (AI-sure or unsure)
    "no_answer",      # the request ended without a usable answer (see error)
    "late",           # the answer came after the question was answered / closed
    "stale",          # the question changed: the request was stopped or ignored
    "cancelled",      # the question changed before the request even started
}
ERRORS = {"", "rate", "overload", "timeout", "connection", "key", "model_unavailable",
          "http", "bad_reply", "ambiguous", "unavailable", "stale", "internal"}
_CATEGORY = re.compile(r"^[A-ZÁÉÍÓÚÜÑ¿?¡! ]{1,32}$")
_MODEL = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_KIND = {"choice", "order", "pairs", ""}

# name -> (type, check)
_NUM = (float, None)
FIELDS = {
    "category": (str, lambda v: bool(_CATEGORY.match(v))),
    "kind": (str, lambda v: v in _KIND),
    "provider": (str, lambda v: v in PROVIDERS),
    "model": (str, lambda v: bool(_MODEL.match(v))),
    "outcome": (str, lambda v: v in OUTCOMES),
    "error": (str, lambda v: v in ERRORS),
    "status": (str, lambda v: v in {"ai", "unsure", "unknown", ""}),
    "queue_ms": _NUM,          # waiting for a free lane
    "request_ms": _NUM,        # the AI call(s) themselves
    "connect_ms": _NUM,        # new connection set-up (0 = reused)
    "poll_ms": _NUM,           # ready -> shown on screen
    "total_ms": _NUM,          # asked -> shown (what the player waits)
    "calls": (int, None),      # requests sent to the provider for this question
    "tokens_in": (int, None),
    "tokens_out": (int, None),
    "facts": (int, None),
    "reused": (bool, None),
    "reconnected": (bool, None),
    "searched": (bool, None),
    "timed": (bool, None),     # Preguntas express (timed quiz)
}


def clean(record: dict) -> dict:
    """Only allowed fields with allowed values; everything else is dropped."""
    out = {}
    for name, value in record.items():
        spec = FIELDS.get(name)
        if spec is None or value is None:
            continue
        typ, check = spec
        if typ is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = round(min(max(float(value), 0.0), 3_600_000.0), 1)
        elif typ is int:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            value = min(max(value, 0), 10_000_000)
        elif typ is bool:
            if not isinstance(value, bool):
                continue
        elif typ is str:
            if not isinstance(value, str) or (check and not check(value)):
                continue
        out[name] = value
    return out


class DiagnosticsLog:
    def __init__(self, folder: str, max_bytes: int = MAX_BYTES):
        self.folder = folder
        self.path = os.path.join(folder, FILE_NAME)
        self.old_path = os.path.join(folder, "ai_timing.1.jsonl")
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    @classmethod
    def default(cls) -> "DiagnosticsLog":
        """The log in SK Assistant's data folder (only the UI uses this)."""
        from .config import DATA_DIR
        return cls(os.path.join(DATA_DIR, "diagnostics"))

    def log(self, **record) -> Optional[dict]:
        rec = clean(record)
        rec = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), **rec}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        try:
            with self._lock:
                os.makedirs(self.folder, exist_ok=True)
                try:
                    size = os.path.getsize(self.path)
                except OSError:
                    size = 0
                if size and size + len(line.encode("utf-8")) > self.max_bytes:
                    os.replace(self.path, self.old_path)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception:
            return None
        return rec

    def read(self) -> list:
        """All logged records, oldest first (for a summary or an export)."""
        rows = []
        for p in (self.old_path, self.path):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass
            except OSError:
                pass
        return rows
