"""SK AI Speed Check - a standalone, read-only timing benchmark for the Gemini AI
that SK Assistant uses for quiz questions.

What it does
  * Reads your saved Gemini key from SK Assistant's encrypted key file
    (%APPDATA%\\SK Assistant\\keys.bin) - read only, decrypted in memory with
    your Windows account. The key is sent ONLY to Google's Gemini API
    (generativelanguage.googleapis.com) in the request header, exactly like
    SK Assistant does. It is never printed, shown or written to any file.
  * Asks Google which Gemini models your key can use.
  * Sends about 20 test questions per comparison (the same request format SK
    Assistant uses), measures the time to the first response and the total
    time, and checks the answers against the known right answers.
  * Writes a report next to this script (results\\ folder).

What it does NOT do
  * It does not start, change or talk to SK Assistant.
  * It does not open habbosk.db, atlas.db or knowledge.db.
  * It never writes anything into %APPDATA%\\SK Assistant.
  * It uses only Python's standard library (nothing to install).

Run:  AI_SPEED_CHECK.bat   (or:  py ai_speed_check.py  [--quick])
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import http.client
import json
import os
import re
import socket
import ssl
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

VERSION = "1.0"
HOST = "generativelanguage.googleapis.com"      # the ONLY host the key is ever sent to
API = "/v1beta"
SK_TIMEOUT_LIBRARY, SK_TIMEOUT_EXPRESS = 8.0, 6.0   # SK Assistant's own time limits
REQUEST_TIMEOUT = 20.0                               # longer, so slow answers are measured
LETTERS = "ABCDEFGHIJ"
CHOICE, ORDER, PAIRS = "choice", "order", "pairs"


# ---------------------------------------------------------------------------
# The request SK Assistant sends (copied from puzzle_assistant/qa/ai.py v3.3;
# tests/test_ai_speed_check.py checks that this copy matches the program).
# ---------------------------------------------------------------------------
SYSTEM = (
    "You answer quiz questions shown in a Spanish-language Habbo Hotel game, for a player "
    "who may have learning difficulties. Read the WHOLE question carefully: words like "
    "'error', 'falsa', 'incorrecta', 'NO', 'excepto' mean you must pick the WRONG statement. "
    "Hint lines are clues, never options. Many questions are about this particular game's "
    "own world (its characters, places, items, rules); if you cannot know the answer, say so "
    "with a low confidence instead of guessing. Facts listed as confirmed by this game are "
    "true: use them when they are relevant, ignore them when they are not. "
    "Reply with JSON only, no other text.")


@dataclass
class Q:
    category: str
    text: str
    options: List[str]
    kind: str = CHOICE
    clues: List[str] = field(default_factory=list)
    right: List[str] = field(default_factory=list)
    language: str = "es"
    answer: object = None            # option text / list / dict - the game's right answer
    world: bool = False              # about the game's own world (an AI can't really know)


def build_prompt(q: Q, snippets: Optional[List[str]] = None,
                 facts: Optional[List[str]] = None) -> str:
    lines = []
    if facts:
        lines.append("Facts confirmed by this game (from earlier questions):")
        lines += [f"  - {f}" for f in facts]
    lines += [f"Category: {q.category or '-'}", f"Question: {q.text}"]
    for c in q.clues:
        lines.append(f"Clue: {c}")
    if q.kind == PAIRS:
        lines.append("Left items:")
        lines += [f"  {LETTERS[i]}. {o}" for i, o in enumerate(q.options)]
        lines.append("Right items:")
        lines += [f"  {i + 1}. {o}" for i, o in enumerate(q.right)]
        fmt = ('{"pairs": {"A": "<right number>", ...one per left item}, '
               '"explanation": "<one short sentence>", "confidence": <0..1>}')
    else:
        lines.append("Options:")
        lines += [f"  {LETTERS[i]}. {o}" for i, o in enumerate(q.options)]
        if q.kind == ORDER:
            fmt = ('{"order": ["<letter>", ... every option once, in the requested order], '
                   '"explanation": "<one short sentence>", "confidence": <0..1>}')
        else:
            fmt = ('{"answer": "<one letter>", "explanation": "<one short sentence>", '
                   '"confidence": <0..1>}')
    if snippets:
        lines.append("Web search results (may help, may be irrelevant):")
        lines += [f"  - {s[:400]}" for s in snippets[:5]]
    lang = "Spanish" if q.language != "en" else "English"
    lines.append(f"Reply with exactly this JSON: {fmt}. Write the explanation in {lang}.")
    return "\n".join(lines)


def parse_reply(text: str, q: Q) -> Tuple[object, float]:
    """(answer in option texts, confidence) - the same rules SK uses.
    Raises ValueError when the reply doesn't fit the options."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the reply")
    data = json.loads(m.group(0))
    conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    n = len(q.options)
    if q.kind == CHOICE:
        letter = str(data.get("answer", "")).strip().upper()[:1]
        if letter not in LETTERS[:n]:
            raise ValueError(f"answer {letter!r} is not an option")
        return q.options[LETTERS.index(letter)], conf
    if q.kind == ORDER:
        order = [str(x).strip().upper()[:1] for x in data.get("order", [])]
        if sorted(order) != list(LETTERS[:n]):
            raise ValueError("order is not a permutation of the options")
        return [q.options[LETTERS.index(x)] for x in order], conf
    pairs, used = {}, set()
    for k, v in data.get("pairs", {}).items():
        k = str(k).strip().upper()[:1]
        j = int(str(v).strip().split(".")[0]) - 1
        if k not in LETTERS[:n] or not 0 <= j < len(q.right) or j in used:
            raise ValueError("pairs don't match the items")
        used.add(j)
        pairs[q.options[LETTERS.index(k)]] = q.right[j]
    if len(pairs) != n:
        raise ValueError("not every item was paired")
    return pairs, conf


def request_body(q: Q, thinking: Optional[dict] = None) -> dict:
    """SK Assistant's Gemini request body (Gemini.ask), optionally with a thinking setting."""
    gen = {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 1024}
    if thinking:
        gen["thinkingConfig"] = dict(thinking)
    return {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": build_prompt(q)}]}],
            "generationConfig": gen}


# ---------------------------------------------------------------------------
# 20 representative questions with known right answers:
# 12 = SK's starter questions (answers revealed by the game, assets/seed_questions.json),
#  8 = general-knowledge questions like the timed "Preguntas express" quiz.
# ---------------------------------------------------------------------------
EXPRESS = "PREGUNTAS EXPRESS"
QUESTIONS: List[Q] = [
    Q("ADIVINANZA", "Parezco un regalo, pero escondía soldados griegos para conquistar una ciudad. ¿Qué soy?",
      ["La espada de Aquiles", "El escudo de Esparta", "El barco de Ulises", "El caballo de Troya"],
      answer="El caballo de Troya"),
    Q("ORDENAR", "Ordena las cuentas de menor a mayor resultado", ["9 + 12", "21 + 10", "63 − 8", "36 + 10"],
      kind=ORDER, answer=["9 + 12", "21 + 10", "36 + 10", "63 − 8"]),
    Q("ADIVINANZA", "Soy un boleto instantáneo que se compra en cualquier lugar con :raspadito. ¿Qué soy?",
      ["Una mesa de UNO", "El Casino de la Playa", "El raspadito", "La tragamonedas de palanca"],
      answer="El raspadito", world=True),
    Q("¿QUIÉN SOY?", "Soy un asistente NPC", ["Rubén", "Mateo", "Tomás", "Leo"], answer="Leo", world=True),
    Q("ENCUENTRA EL ERROR", "¿Qué frase tiene un error?",
      ["Las tragamonedas del Gran Casino tienen tres rodillos.", "El raspadito es un boleto instantáneo.",
       "El Casino de la Playa tiene tragamonedas de palanca.",
       "Las tragamonedas siempre devuelven más de lo apostado."],
      answer="Las tragamonedas siempre devuelven más de lo apostado.", world=True),
    Q("PREGUNTA", "¿Qué letra sigue?", ["U", "X", "W", "V"], clues=["F, J, N, R, ?"], answer="V"),
    Q("PAREJAS", "Une cada término de la prisión con su descripción",
      ["Fianza", "Fuga", "Talleres", "Libertad condicional"],
      kind=PAIRS, right=["La paga otro jugador", "Los minutos tras salir", "Cavar en la celda", "Restan condena"],
      answer={"Fianza": "La paga otro jugador", "Fuga": "Cavar en la celda", "Talleres": "Restan condena",
              "Libertad condicional": "Los minutos tras salir"}, world=True),
    Q("PAREJAS", "Une cada máquina del gimnasio con su minijuego",
      ["Cinta de correr", "Elíptica", "Banco de press", "Sentadilla"],
      kind=PAIRS, right=["Equilibrar la barra", "Cadencia dentro de la franja", "Empujar la barra hasta arriba",
                         "Saltar vallas y esquivar conos"],
      answer={"Cinta de correr": "Saltar vallas y esquivar conos", "Elíptica": "Cadencia dentro de la franja",
              "Banco de press": "Empujar la barra hasta arriba", "Sentadilla": "Equilibrar la barra"},
      world=True),
    Q("PAREJAS", "Une cada animal con su alimento favorito", ["Panda", "Koala", "Vaca", "Abeja"],
      kind=PAIRS, right=["Pasto", "Néctar", "Eucalipto", "Bambú"],
      answer={"Panda": "Bambú", "Koala": "Eucalipto", "Vaca": "Pasto", "Abeja": "Néctar"}),
    Q("ENCUENTRA EL ERROR", "¿Qué frase tiene un error?",
      ["No existen transferencias de banco a banco entre jugadores.",
       "El dinero se entrega en mano, en el mismo lugar.",
       "Con :dar puedes entregar dinero que tienes en el banco.",
       "No se puede dar dinero a otra de tus cuentas."],
      answer="Con :dar puedes entregar dinero que tienes en el banco.", world=True),
    Q("ORDENAR", "Ordena los peces de menor a mayor precio de referencia",
      ["Pulpo", "Sardina", "Atún", "Pez espada", "Dorada"],
      kind=ORDER, answer=["Sardina", "Dorada", "Pulpo", "Atún", "Pez espada"], world=True),
    Q("ADIVINANZA", "Estudias 45 minutos y luego 25 minutos más. ¿Cuántos minutos estudiaste?",
      ["79", "69", "70", "71"], answer="70"),
    Q(EXPRESS, "¿Cuál es el planeta más grande del sistema solar?", ["Marte", "Júpiter", "Saturno", "Venus"],
      answer="Júpiter"),
    Q(EXPRESS, "¿En qué país están las pirámides de Guiza?", ["México", "Perú", "Egipto", "Grecia"],
      answer="Egipto"),
    Q(EXPRESS, "¿Cuál es el océano más grande del mundo?", ["Atlántico", "Índico", "Ártico", "Pacífico"],
      answer="Pacífico"),
    Q(EXPRESS, "¿Quién pintó la Mona Lisa?",
      ["Pablo Picasso", "Leonardo da Vinci", "Vincent van Gogh", "Salvador Dalí"], answer="Leonardo da Vinci"),
    Q(EXPRESS, "¿Cuál es el símbolo químico del oro?", ["Ag", "Fe", "Au", "Or"], answer="Au"),
    Q(EXPRESS, "¿Cuántos lados tiene un hexágono?", ["5", "6", "7", "8"], answer="6"),
    Q(EXPRESS, "¿Cuál es la capital de Japón?", ["Pekín", "Seúl", "Tokio", "Bangkok"], answer="Tokio"),
    Q(EXPRESS, "¿Qué gas toman las plantas del aire para hacer la fotosíntesis?",
      ["Oxígeno", "Dióxido de carbono", "Nitrógeno", "Helio"], answer="Dióxido de carbono"),
]


# ---------------------------------------------------------------------------
# The key: read-only, never shown
# ---------------------------------------------------------------------------
def sk_data_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SK Assistant")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "SK Assistant")


def _dpapi_decrypt(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: never show a dialog
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                                    0x1, ctypes.byref(blob_out)):
        raise OSError("Windows could not decrypt the key file (other Windows account?)")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def read_gemini_key(path: str) -> Optional[str]:
    """SK Assistant's saved Gemini key, or None. Opens the file read-only."""
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"DPAPI:"):
        raw = _dpapi_decrypt(raw[6:])
    elif raw.startswith(b"PLAIN:"):                  # SK's non-Windows development format
        raw = base64.b64decode(raw[6:])
    else:
        return None
    key = json.loads(raw.decode("utf-8")).get("ai:gemini") or None
    return key.strip() if key else None


def read_sk_model_setting(path: str) -> str:
    """The forced model in SK's config.json ("ai_model"), "" = SK chooses itself. Read-only."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("ai_model") or "").strip()
    except Exception:
        return ""


class Redactor:
    """Removes the key from any text before it is shown or written."""

    def __init__(self, secret: str):
        self.secret = secret or ""

    def __call__(self, text) -> str:
        s = str(text)
        if self.secret and len(self.secret) >= 4:
            s = s.replace(self.secret, "[key hidden]")
        return s


# ---------------------------------------------------------------------------
# Talking to Gemini (standard library only)
# ---------------------------------------------------------------------------
@dataclass
class Reply:
    status: int = 0
    data: dict = field(default_factory=dict)
    text: str = ""                   # model text (non-thought parts)
    connect_ms: float = 0.0          # new connection set-up (0 when reused)
    first_ms: float = 0.0            # submission -> first response byte
    total_ms: float = 0.0            # submission -> complete answer
    first_text_ms: float = 0.0       # streaming: submission -> first text
    letter_ms: float = 0.0           # streaming: submission -> answer letter readable
    error: str = ""


class Gemini:
    """One HTTPS connection to Gemini. fresh=True opens a new connection for every
    request (what SK Assistant does today); fresh=False keeps one open."""

    def __init__(self, key: str, fresh: bool, timeout: float = REQUEST_TIMEOUT):
        self._key, self.fresh, self.timeout = key, fresh, timeout
        self.conn: Optional[http.client.HTTPSConnection] = None
        self.ctx = ssl.create_default_context()

    def _new(self) -> http.client.HTTPSConnection:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
        p = urlparse(proxy) if proxy else None
        if p and p.hostname:
            c = http.client.HTTPSConnection(p.hostname, p.port or 8080, context=self.ctx, timeout=self.timeout)
            c.set_tunnel(HOST, 443)
        else:
            c = http.client.HTTPSConnection(HOST, 443, context=self.ctx, timeout=self.timeout)
        return c

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def call(self, method: str, path: str, body: Optional[dict] = None, stream: bool = False) -> Reply:
        r = Reply()
        t0 = time.perf_counter()
        try:
            if self.fresh:
                self.close()
            if self.conn is None:
                self.conn = self._new()
                self.conn.connect()
                r.connect_ms = (time.perf_counter() - t0) * 1000
            headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            self.conn.request(method, API + path, body=payload, headers=headers)
            resp = self.conn.getresponse()
            r.status = resp.status
            if stream and resp.status == 200:
                self._read_stream(resp, r, t0)
            else:
                first = resp.read(1)
                r.first_ms = (time.perf_counter() - t0) * 1000
                raw = first + resp.read()
                r.total_ms = (time.perf_counter() - t0) * 1000
                try:
                    r.data = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    r.data = {}
                parts = ((r.data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                         if isinstance(r.data, dict) else [])
                r.text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if resp.getheader("Connection", "").lower() == "close":
                self.close()
        except (socket.timeout, TimeoutError):
            r.error = "timeout"
            self.close()
        except (OSError, http.client.HTTPException) as exc:
            r.error = f"connection: {exc.__class__.__name__}"
            self.close()
        if not r.total_ms:
            r.total_ms = (time.perf_counter() - t0) * 1000
        return r

    def _read_stream(self, resp, r: Reply, t0: float) -> None:
        text, usage = "", {}
        while True:
            line = resp.readline()
            if not line:
                break
            if not r.first_ms:
                r.first_ms = (time.perf_counter() - t0) * 1000
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            try:
                chunk = json.loads(line[5:].decode("utf-8"))
            except ValueError:
                continue
            usage = chunk.get("usageMetadata", usage)
            parts = (chunk.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            piece = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if piece and not r.first_text_ms:
                r.first_text_ms = (time.perf_counter() - t0) * 1000
            text += piece
            if not r.letter_ms and re.search(r'"answer"\s*:\s*"[A-J]"', text):
                r.letter_ms = (time.perf_counter() - t0) * 1000
        r.total_ms = (time.perf_counter() - t0) * 1000
        r.text = text
        r.data = {"usageMetadata": usage}


def http_error(r: Reply) -> str:
    if r.error:
        return r.error
    msg = (r.data.get("error") or {}).get("message", "") if isinstance(r.data, dict) else ""
    if r.status == 429:
        return "rate limit (429)"
    return f"HTTP {r.status}: {msg[:100]}"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
SKIP = r"preview|exp|image|tts|live|audio|thinking|embed|vision"


def _version(n: str) -> float:
    v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    return float(v.group(1)) if v else 0.0


def usable_models(models: List[dict]) -> List[str]:
    """Stable models that can answer text (SK Assistant's own filter)."""
    names = []
    for m in models:
        name = m.get("name", "").split("/")[-1]
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if re.search(SKIP, name):
            continue
        names.append(name)
    return names


def sk_pick(names: List[str]) -> Optional[str]:
    """The model SK Assistant would choose today (Gemini._pick_model)."""
    for kind in ("flash-lite", "flash"):
        cands = sorted((n for n in names if n.endswith(kind) or f"{kind}-0" in n
                        or n.endswith(kind + "-latest")), key=_version, reverse=True)
        if cands:
            return cands[0]
    return None


def flash_lite_models(names: List[str]) -> List[str]:
    """Stable Flash-Lite models, newest first, without '-latest' aliases."""
    out = [n for n in names if "flash-lite" in n and not n.endswith("-latest")]
    return sorted(out, key=lambda n: (_version(n), n), reverse=True)


THINKING_OFF = ({"thinkingBudget": 0}, {"thinkingLevel": "minimal"}, {"thinkingLevel": "low"})


# ---------------------------------------------------------------------------
# Running one comparison
# ---------------------------------------------------------------------------
@dataclass
class Row:
    q: int
    category: str
    world: bool
    ok: bool = False                 # a usable answer came back
    correct: bool = False
    confidence: float = 0.0
    connect_ms: float = 0.0
    first_ms: float = 0.0
    total_ms: float = 0.0
    first_text_ms: float = 0.0
    letter_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thinking: int = 0
    error: str = ""
    rate_limited: bool = False


def same_answer(got, want) -> bool:
    return got == want


class Pacer:
    """Keeps requests at least `gap` seconds apart (Gemini's free tier allows only
    a few requests per minute)."""

    def __init__(self, gap: float):
        self.gap, self.last = gap, 0.0

    def wait(self) -> None:
        d = self.gap - (time.monotonic() - self.last)
        if d > 0:
            time.sleep(d)
        self.last = time.monotonic()


def ask(client: Gemini, model: str, q: Q, thinking: Optional[dict], stream: bool, pacer: Pacer,
        say, rate_wait: float = 65.0) -> Row:
    row = Row(0, q.category, q.world)
    path = f"/models/{model}:" + ("streamGenerateContent?alt=sse" if stream else "generateContent")
    for attempt in (0, 1):
        pacer.wait()
        r = client.call("POST", path, request_body(q, thinking), stream=stream)
        if r.status == 429 and attempt == 0:
            row.rate_limited = True
            say(f"      Google's per-minute limit reached - waiting {rate_wait:.0f} s, then trying again")
            time.sleep(rate_wait)
            continue
        break
    row.connect_ms, row.first_ms, row.total_ms = r.connect_ms, r.first_ms, r.total_ms
    row.first_text_ms, row.letter_ms = r.first_text_ms, r.letter_ms
    u = r.data.get("usageMetadata", {}) if isinstance(r.data, dict) else {}
    row.tokens_in = int(u.get("promptTokenCount", 0) or 0)
    row.tokens_out = int(u.get("candidatesTokenCount", 0) or 0)
    row.tokens_thinking = int(u.get("thoughtsTokenCount", 0) or 0)
    if r.error or r.status != 200:
        row.error = http_error(r)
        return row
    try:
        got, row.confidence = parse_reply(r.text, q)
        row.ok = True
        row.correct = same_answer(got, q.answer)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        row.error = f"reply not usable: {exc}"
    return row


def pct(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(rows: List[Row]) -> dict:
    done = [r for r in rows if not r.error]
    tot = [r.total_ms for r in done]
    first = [r.first_ms for r in done]
    s = {"requests": len(rows), "answered": sum(r.ok for r in rows),
         "errors": len(rows) - len(done) + sum(1 for r in done if not r.ok),
         "correct": sum(r.correct for r in rows),
         "correct_general": sum(r.correct for r in rows if not r.world),
         "n_general": sum(1 for r in rows if not r.world),
         "correct_world": sum(r.correct for r in rows if r.world),
         "n_world": sum(1 for r in rows if r.world),
         "rate_limited": sum(r.rate_limited for r in rows),
         "first_median_ms": statistics.median(first) if first else 0.0,
         "total_median_ms": statistics.median(tot) if tot else 0.0,
         "total_p90_ms": pct(tot, 0.9), "total_min_ms": min(tot) if tot else 0.0,
         "total_max_ms": max(tot) if tot else 0.0,
         "connect_median_ms": statistics.median([r.connect_ms for r in done]) if done else 0.0,
         "over_sk_limit": sum(1 for r in done if r.total_ms > 1000 * (
             SK_TIMEOUT_EXPRESS if r.category == EXPRESS else SK_TIMEOUT_LIBRARY)),
         "thinking_tokens_avg": (sum(r.tokens_thinking for r in done) / len(done)) if done else 0.0,
         "output_tokens_avg": (sum(r.tokens_out for r in done) / len(done)) if done else 0.0,
         "error_kinds": sorted({r.error.split(":")[0] for r in rows if r.error})}
    st = [r for r in done if r.first_text_ms]
    if st:
        s["stream_first_text_median_ms"] = statistics.median([r.first_text_ms for r in st])
        lt = [r.letter_ms for r in st if r.letter_ms]
        s["stream_letter_median_ms"] = statistics.median(lt) if lt else 0.0
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SK AI Speed Check (read-only Gemini timing benchmark)")
    ap.add_argument("--quick", action="store_true", help="only SK's current set-up (about 20 requests)")
    ap.add_argument("--full", action="store_true", help="also try 'thinking off' on the alternatives")
    ap.add_argument("--requests", type=int, default=20, help="questions per comparison (max 20)")
    ap.add_argument("--current-model", default="", help="the model SK shows under 'Test AI' (optional)")
    ap.add_argument("--pace", type=float, default=4.5, help="seconds between requests (free-tier limit)")
    ap.add_argument("--yes", action="store_true", help="start without asking")
    ap.add_argument("--out", default="", help="folder for the report (default: results next to this script)")
    ap.add_argument("--keys-file", default="", help=argparse.SUPPRESS)       # tests only
    ap.add_argument("--config-file", default="", help=argparse.SUPPRESS)     # tests only
    ap.add_argument("--rate-wait", type=float, default=65.0, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    n = max(1, min(20, a.requests))
    questions = QUESTIONS[:n]
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = a.out or os.path.join(here, "results")
    keys_path = a.keys_file or os.path.join(sk_data_dir(), "keys.bin")
    cfg_path = a.config_file or os.path.join(sk_data_dir(), "config.json")

    print(f"SK AI Speed Check {VERSION} - read-only, nothing in SK Assistant is changed.\n")
    try:
        key = read_gemini_key(keys_path)
    except Exception as exc:
        print(f"Could not read SK Assistant's saved key: {exc.__class__.__name__}. Nothing was sent.")
        return 2
    if not key:
        print("No Gemini key is saved in SK Assistant (Settings > AI and internet). Nothing was sent.")
        return 2
    hide = Redactor(key)
    log: List[str] = []

    def say(msg: str = "") -> None:
        msg = hide(msg)
        print(msg)
        log.append(msg)

    say("Your saved Gemini key was found (it is not shown and will not be saved).")
    forced = a.current_model.strip() or read_sk_model_setting(cfg_path)

    # 1. which models can this key use?
    lister = Gemini(key, fresh=True)
    r = lister.call("GET", "/models?pageSize=200")
    lister.close()
    if r.status != 200:
        say(f"Google did not list the models: {http_error(r)}. Nothing else was sent.")
        return 3
    names = usable_models(r.data.get("models", []))
    info = {m.get("name", "").split("/")[-1]: m for m in r.data.get("models", [])}
    pick = sk_pick(names)
    lites = flash_lite_models(names)
    current = forced or pick
    if not current:
        say("No suitable Gemini model is available for this key.")
        return 3
    say(f"Models your key can use (stable Flash-Lite): {', '.join(lites) or 'none'}")
    say(f"SK Assistant would choose today: {pick}")
    if forced:
        say(f"Model set for this check: {forced}")
    else:
        say("Note: SK remembers the model it chose the first time. If 'Test AI' in SK's Settings\n"
            "shows a different model, run again with:  --current-model <that name>")
    alternatives = [] if a.quick else [m for m in lites if m != current][:2]

    plan: List[Tuple[str, str, bool, Optional[dict], bool]] = [
        ("SK today (new connection each time)", current, True, None, False)]
    if not a.quick:
        plan.append(("SK today + reused connection", current, False, None, False))
        plan.append(("SK today, streamed (first-text timing)", current, False, None, True))
        plan.append(("SK today + thinking off", current, False, "auto", False))
        for m in alternatives:
            plan.append((f"alternative {m} + reused connection", m, False, None, False))
            if a.full:
                plan.append((f"alternative {m} + thinking off", m, False, "auto", False))
    n_stream = sum(1 for q in questions if q.kind == CHOICE)
    planned = sum((min(10, n_stream) if p[4] else len(questions)) for p in plan) + 1 \
        + len(THINKING_OFF) * sum(1 for p in plan if p[3] == "auto")
    say(f"\nPlan: {len(plan)} comparisons, up to {planned} requests to Google, about "
        f"{planned * a.pace / 60:.0f} minutes. Gemini's free tier costs nothing.")
    say("SK Assistant can stay open; avoid quiz questions during the check (they share Google's limit).")
    if not a.yes:
        try:
            input("\nPress Enter to start (or close this window to cancel)... ")
        except EOFError:
            pass

    pacer = Pacer(a.pace)
    results = []
    started = _dt.datetime.now()
    for title, model, fresh, thinking, stream in plan:
        think_cfg = None
        if thinking == "auto":
            think_cfg = probe_thinking_off(key, model, pacer, say)
            if think_cfg is None:
                say(f"\n[{title}] skipped: this model does not accept a 'thinking off' setting.")
                results.append({"title": title, "model": model, "skipped": "thinking setting not accepted"})
                continue
        qs = [q for q in questions if q.kind == CHOICE][:10] if stream else questions
        say(f"\n[{title}]  model {model}" + (f", thinking {json.dumps(think_cfg)}" if think_cfg else ""))
        client = Gemini(key, fresh=fresh)
        rows = []
        for i, q in enumerate(qs, 1):
            row = ask(client, model, q, think_cfg, stream, pacer, say, a.rate_wait)
            row.q = QUESTIONS.index(q) + 1
            rows.append(row)
            mark = "error" if row.error else ("right" if row.correct else "wrong")
            say(f"   {i:2d}/{len(qs)}  {row.total_ms / 1000:5.2f} s  {mark:5s}"
                + (f"  ({row.error})" if row.error else ""))
        client.close()
        s = summarize(rows)
        results.append({"title": title, "model": model, "fresh_connection": fresh, "streamed": stream,
                        "thinking": think_cfg, "summary": s, "rows": [row.__dict__ for row in rows]})
        say(f"   -> median {s['total_median_ms'] / 1000:.2f} s, slowest {s['total_max_ms'] / 1000:.2f} s, "
            f"right {s['correct']}/{s['requests']}, errors {s['errors']}")

    report = {"tool": f"SK AI Speed Check {VERSION}", "started": started.isoformat(timespec="seconds"),
              "finished": _dt.datetime.now().isoformat(timespec="seconds"),
              "python": sys.version.split()[0], "platform": sys.platform,
              "sk_pick_today": pick, "model_tested_as_current": current, "forced_model": forced,
              "flash_lite_available": lites,
              "model_details": {m: {k: info.get(m, {}).get(k) for k in
                                    ("version", "inputTokenLimit", "outputTokenLimit", "thinking")}
                                for m in sorted(set([current] + alternatives))},
              "questions": len(questions), "results": results}
    path_txt, path_json = write_report(report, out_dir, hide)
    say("\n" + table(results))
    say(f"\nReport saved:\n  {path_txt}\n  {path_json}")
    say("Send these two files to Claude (they contain no key and no personal data).")
    return 0


def probe_thinking_off(key: str, model: str, pacer: Pacer, say) -> Optional[dict]:
    """The first 'thinking off' setting this model accepts (one tiny request each)."""
    q = QUESTIONS[16]                                           # symbol of gold: trivial
    client = Gemini(key, fresh=False)
    try:
        for cfg in THINKING_OFF:
            pacer.wait()
            r = client.call("POST", f"/models/{model}:generateContent", request_body(q, cfg))
            if r.status == 200:
                return cfg
            if r.status == 429:
                return None
        return None
    finally:
        client.close()


def table(results: List[dict]) -> str:
    lines = [f"{'Comparison':52s} {'first':>7s} {'median':>7s} {'p90':>7s} {'slowest':>8s} {'right':>7s} {'err':>4s}"]
    for r in results:
        if "summary" not in r:
            lines.append(f"{r['title'][:52]:52s}  skipped ({r.get('skipped', '')})")
            continue
        s = r["summary"]
        first = s.get("stream_first_text_median_ms") or s["first_median_ms"]
        lines.append(f"{r['title'][:52]:52s} {first / 1000:6.2f}s {s['total_median_ms'] / 1000:6.2f}s "
                     f"{s['total_p90_ms'] / 1000:6.2f}s {s['total_max_ms'] / 1000:7.2f}s "
                     f"{s['correct']:3d}/{s['requests']:<3d} {s['errors']:4d}")
    lines.append("first = time to first response (streamed: first text). Times include the connection.")
    return "\n".join(lines)


def write_report(report: dict, out_dir: str, hide: Redactor) -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    pj = os.path.join(out_dir, f"ai_speed_report_{stamp}.json")
    pt = os.path.join(out_dir, f"ai_speed_report_{stamp}.txt")
    body = hide(json.dumps(report, ensure_ascii=False, indent=1))
    with open(pj, "w", encoding="utf-8") as fh:
        fh.write(body)
    head = [report["tool"], f"started {report['started']}, finished {report['finished']}",
            f"Python {report['python']} on {report['platform']}",
            f"SK would pick today: {report['sk_pick_today']}; tested as current: "
            f"{report['model_tested_as_current']}",
            f"Flash-Lite models available: {', '.join(report['flash_lite_available'])}", ""]
    with open(pt, "w", encoding="utf-8") as fh:
        fh.write(hide("\n".join(head) + table(report["results"]) + "\n"))
    return pt, pj


if __name__ == "__main__":
    code = main()
    if sys.platform == "win32" and "--yes" not in sys.argv:
        try:
            input("\nDone. Press Enter to close... ")
        except EOFError:
            pass
    sys.exit(code)
