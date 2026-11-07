"""AI answers for questions that memory and the built-in solvers can't answer.

Provider-independent: Google Gemini (free tier possible), Anthropic Claude,
OpenAI. Only Python's standard library is used for the web requests.

Cost rules (the owner's requirement: never an unexpected payment):
  * Claude and OpenAI always cost money: they are used only when "Allow paid
    AI use" is on AND this month's estimated spending is below the limit.
  * Gemini can be used on its free tier with paid use off; a daily cap
    (ai_daily_limit) keeps it well under Google's free limits. If the key's
    Google project has billing enabled, Google may charge: the settings say so.
  * "Too many requests" (HTTP 429) pauses AI use for a minute.
  * Every request is counted (Settings shows requests and estimated cost).

An AI answer is never shown as certain: it is labelled "AI · unverified"
and, like every answer, checked against what the game reveals.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .. import secrets
from .model import CHOICE, ORDER, PAIRS, Answer, Question

LETTERS = "ABCDEFGHIJ"
PAID_ONLY = {"anthropic", "openai"}
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5"}
# rough USD per million tokens, used only for the spending estimate (editable in config)
DEFAULT_PRICES = {"gemini": (0.0, 0.0), "anthropic": (1.0, 5.0), "openai": (0.4, 1.6)}

Http = Callable[[str, str, Dict[str, str], Optional[dict], float], Tuple[int, dict]]


def default_http(method: str, url: str, headers: Dict[str, str], body: Optional[dict],
                 timeout: float) -> Tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return exc.code, payload


@dataclass
class AIResult:
    answer: Optional[Answer] = None
    error: str = ""
    provider: str = ""
    model: str = ""
    ms: float = 0.0
    used_search: bool = False
    facts: int = 0                 # learned facts sent along with the question


class AIUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------
# Prompt and reply format
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


def build_prompt(q: Question, snippets: Optional[List[str]] = None,
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


def parse_reply(text: str, q: Question) -> Answer:
    """Turn the model's JSON into an Answer that uses the on-screen option
    texts. Raises ValueError if it doesn't fit the options."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the reply")
    data = json.loads(m.group(0))
    conf = float(data.get("confidence", 0.0))
    conf = max(0.0, min(1.0, conf))
    expl = str(data.get("explanation", "")).strip()
    n = len(q.options)
    if q.kind == CHOICE:
        letter = str(data.get("answer", "")).strip().upper()[:1]
        if letter not in LETTERS[:n]:
            raise ValueError(f"answer {letter!r} is not an option")
        return Answer(CHOICE, choice=q.options[LETTERS.index(letter)], explanation=expl,
                      source="ai", confidence=conf)
    if q.kind == ORDER:
        order = [str(x).strip().upper()[:1] for x in data.get("order", [])]
        if sorted(order) != list(LETTERS[:n]):
            raise ValueError("order is not a permutation of the options")
        return Answer(ORDER, order=[q.options[LETTERS.index(x)] for x in order],
                      explanation=expl, source="ai", confidence=conf)
    pairs_in = data.get("pairs", {})
    pairs = {}
    used = set()
    for k, v in pairs_in.items():
        k = str(k).strip().upper()[:1]
        j = int(str(v).strip().split(".")[0]) - 1
        if k not in LETTERS[:n] or not 0 <= j < len(q.right) or j in used:
            raise ValueError("pairs don't match the items")
        used.add(j)
        pairs[q.options[LETTERS.index(k)]] = q.right[j]
    if len(pairs) != n:
        raise ValueError("not every item was paired")
    return Answer(PAIRS, pairs=pairs, explanation=expl, source="ai", confidence=conf)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class Provider:
    name = ""
    key_name = ""

    def __init__(self, cfg, memory, http: Http):
        self.cfg, self.memory, self.http = cfg, memory, http

    def key(self) -> Optional[str]:
        return secrets.get_key(self.key_name)

    def model(self) -> str:
        return self.cfg.ai_model or DEFAULT_MODELS.get(self.name, "")

    def ask(self, system: str, user: str, timeout: float) -> Tuple[str, int, int]:
        """Returns (reply text, input tokens, output tokens)."""
        raise NotImplementedError


class Gemini(Provider):
    name, key_name = "gemini", "ai:gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def model(self) -> str:
        if self.cfg.ai_model:
            return self.cfg.ai_model
        cached = self.memory.get_meta("gemini_model") if self.memory else None
        if cached:
            return cached
        chosen = self._pick_model()
        if self.memory:
            self.memory.set_meta("gemini_model", chosen)
        return chosen

    def _pick_model(self) -> str:
        """Newest stable Flash-Lite (fast and cheap) the key can use."""
        status, data = self.http("GET", f"{self.BASE}/models?pageSize=200",
                                 {"x-goog-api-key": self.key() or ""}, None, 8.0)
        if status != 200:
            raise AIUnavailable(_gemini_error(status, data))
        names = []
        for m in data.get("models", []):
            name = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if re.search(r"preview|exp|image|tts|live|audio|thinking|embed|vision", name):
                continue
            names.append(name)

        def version(n):
            v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
            return float(v.group(1)) if v else 0.0
        for kind in ("flash-lite", "flash"):
            cands = sorted((n for n in names if n.endswith(kind) or f"{kind}-0" in n
                            or n.endswith(kind + "-latest")),
                           key=version, reverse=True)
            if cands:
                return cands[0]
        raise AIUnavailable("no suitable Gemini model is available for this key")

    def ask(self, system, user, timeout):
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                     "maxOutputTokens": 1024}}
        for attempt in (0, 1):
            model = self.model()
            status, data = self.http("POST", f"{self.BASE}/models/{model}:generateContent",
                                     {"x-goog-api-key": self.key() or ""}, body, timeout)
            if status == 404 and attempt == 0 and not self.cfg.ai_model and self.memory:
                self.memory.set_meta("gemini_model", "")      # model retired: pick again
                continue
            break
        if status != 200:
            raise AIUnavailable(_gemini_error(status, data))
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = data.get("usageMetadata", {})
        out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
        return text, u.get("promptTokenCount", 0), out


def _gemini_error(status: int, data: dict) -> str:
    msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
    if status == 429:
        return "rate"
    if status in (400, 401, 403) and ("API key" in msg or "API_KEY" in msg or status != 400):
        return "the Gemini key was not accepted"
    return f"Gemini error {status}: {msg[:120]}"


class Anthropic(Provider):
    name, key_name = "anthropic", "ai:anthropic"

    def ask(self, system, user, timeout):
        body = {"model": self.model(), "max_tokens": 400, "temperature": 0, "system": system,
                "messages": [{"role": "user", "content": user}]}
        status, data = self.http("POST", "https://api.anthropic.com/v1/messages",
                                 {"x-api-key": self.key() or "", "anthropic-version": "2023-06-01"},
                                 body, timeout)
        if status == 429:
            raise AIUnavailable("rate")
        if status != 200:
            raise AIUnavailable(f"Claude error {status}: "
                                f"{(data.get('error') or {}).get('message', '')[:120]}")
        text = "".join(c.get("text", "") for c in data.get("content", []))
        u = data.get("usage", {})
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)


class OpenAI(Provider):
    name, key_name = "openai", "ai:openai"
    PREFERRED = ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini")

    def model(self) -> str:
        if self.cfg.ai_model:
            return self.cfg.ai_model
        cached = self.memory.get_meta("openai_model") if self.memory else None
        if cached:
            return cached
        status, data = self.http("GET", "https://api.openai.com/v1/models",
                                 {"Authorization": f"Bearer {self.key() or ''}"}, None, 8.0)
        if status != 200:
            raise AIUnavailable(f"OpenAI error {status}")
        ids = [m.get("id", "") for m in data.get("data", [])]
        chosen = next((p for p in self.PREFERRED if p in ids), None) or \
            next((i for i in sorted(ids, reverse=True) if i.endswith("-mini")), None)
        if not chosen:
            raise AIUnavailable("no suitable OpenAI model found; set ai_model in config.json")
        if self.memory:
            self.memory.set_meta("openai_model", chosen)
        return chosen

    def ask(self, system, user, timeout):
        body = {"model": self.model(), "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        status, data = self.http("POST", "https://api.openai.com/v1/chat/completions",
                                 {"Authorization": f"Bearer {self.key() or ''}"}, body, timeout)
        if status == 429:
            raise AIUnavailable("rate")
        if status != 200:
            raise AIUnavailable(f"OpenAI error {status}: "
                                f"{(data.get('error') or {}).get('message', '')[:120]}")
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        u = data.get("usage", {})
        return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


PROVIDERS = {"gemini": Gemini, "anthropic": Anthropic, "openai": OpenAI}


# ---------------------------------------------------------------------------
# Internet search (optional)
# ---------------------------------------------------------------------------
def web_search(cfg, query: str, http: Http) -> List[str]:
    p = cfg.search_provider
    if p == "tavily":
        key = secrets.get_key("search:tavily")
        status, data = http("POST", "https://api.tavily.com/search",
                            {"Authorization": f"Bearer {key or ''}"},
                            {"query": query, "max_results": 4}, 6.0)
        if status != 200:
            raise AIUnavailable(f"search error {status}")
        return [f"{r.get('title', '')}: {r.get('content', '')}" for r in data.get("results", [])]
    if p == "brave":
        key = secrets.get_key("search:brave")
        url = "https://api.search.brave.com/res/v1/web/search?count=4&q=" + \
            urllib.request.quote(query)
        status, data = http("GET", url, {"X-Subscription-Token": key or ""}, None, 6.0)
        if status != 200:
            raise AIUnavailable(f"search error {status}")
        return [f"{r.get('title', '')}: {r.get('description', '')}"
                for r in data.get("web", {}).get("results", [])]
    return []


# ---------------------------------------------------------------------------
# The answerer: availability, cost control, one call (+ optional search)
# ---------------------------------------------------------------------------
class AIAnswerer:
    def __init__(self, cfg, memory, http: Optional[Http] = None):
        self.cfg, self.memory = cfg, memory
        self.http = http or default_http
        self.paused_until = 0.0
        self.atlas = None                       # world_atlas.db.Atlas, set by the UI

    def provider(self) -> Optional[Provider]:
        cls = PROVIDERS.get(self.cfg.ai_provider)
        return cls(self.cfg, self.memory, self.http) if cls else None

    def status(self) -> Tuple[bool, str]:
        """(can ask now, reason in plain words if not)."""
        p = self.provider()
        if p is None:
            return False, "AI is switched off (Settings)"
        if not p.key():
            return False, "no API key saved for the chosen AI"
        if time.monotonic() < self.paused_until:
            return False, "AI paused for a minute (too many requests)"
        if p.name in PAID_ONLY and not self.cfg.paid_api:
            return False, "this AI is paid; paid use is off (Settings)"
        if self.cfg.paid_api and p.name in PAID_ONLY:
            spent = self.memory.month_usage()[1] if self.memory else 0.0
            if spent >= float(self.cfg.monthly_limit or 0.0):
                return False, "monthly spending limit reached"
        if not self.cfg.paid_api and self._today() >= int(self.cfg.ai_daily_limit or 0):
            return False, "daily free-use limit reached"
        return True, ""

    def _today(self) -> int:
        if not self.memory:
            return 0
        return int(self.memory.get_meta("ai_day:" + _dt.date.today().isoformat()) or 0)

    def _count(self, provider: str, tin: int, tout: int) -> None:
        if not self.memory:
            return
        pin, pout = self._prices(provider)
        cost = (tin * pin + tout * pout) / 1e6
        self.memory.add_usage(provider, 1, cost)
        k = "ai_day:" + _dt.date.today().isoformat()
        self.memory.set_meta(k, str(self._today() + 1))

    def _prices(self, provider: str) -> Tuple[float, float]:
        if self.cfg.ai_price_in or self.cfg.ai_price_out:
            return float(self.cfg.ai_price_in), float(self.cfg.ai_price_out)
        return DEFAULT_PRICES.get(provider, (0.0, 0.0))

    def answer(self, q: Question, timeout: float = 8.0, allow_search: bool = True) -> AIResult:
        t0 = time.perf_counter()
        ok, why = self.status()
        if not ok:
            return AIResult(error=why)
        p = self.provider()
        res = AIResult(provider=p.name)
        try:
            facts = self.memory.related(q) if self.memory else []
            if self.atlas is not None:          # map / room knowledge from the World Atlas
                try:
                    facts += self.atlas.facts_for(" ".join([q.text] + q.options + q.clues))
                except Exception:
                    pass
            res.facts = len(facts)
            text, tin, tout = p.ask(SYSTEM, build_prompt(q, facts=facts), timeout)
            self._count(p.name, tin, tout)
            res.model = p.model()
            ans = parse_reply(text, q)
            if (allow_search and self.cfg.web_search and self.cfg.search_provider != "none"
                    and ans.confidence < 0.7):
                snippets = self._search(q)
                if snippets:
                    text, tin, tout = p.ask(SYSTEM, build_prompt(q, snippets, facts), timeout)
                    self._count(p.name, tin, tout)
                    ans2 = parse_reply(text, q)
                    if ans2.confidence >= ans.confidence:
                        ans, res.used_search = ans2, True
                        ans.source = "search"
            res.answer = ans
        except AIUnavailable as exc:
            if str(exc) == "rate":
                self.paused_until = time.monotonic() + 60
                res.error = "the AI is busy (free limit per minute); trying again soon"
            else:
                res.error = str(exc)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            res.error = f"the AI's reply could not be used ({exc})"
        except OSError as exc:
            res.error = f"no connection to the AI ({exc.__class__.__name__})"
        res.ms = (time.perf_counter() - t0) * 1000
        return res

    def _search(self, q: Question) -> List[str]:
        try:
            snippets = web_search(self.cfg, q.text + " " + " ".join(q.options), self.http)
            if snippets and self.memory:
                self.memory.add_usage("search:" + self.cfg.search_provider, 1, 0.0)
            return snippets
        except (AIUnavailable, OSError):
            return []

    def test(self) -> str:
        """For the Settings 'Test' button: one tiny question."""
        q = Question("PREGUNTA", "¿Cuál es la capital de Francia?", ["Madrid", "París", "Roma"])
        r = self.answer(q, timeout=10.0, allow_search=False)
        if r.answer is None:
            return f"Not working: {r.error}"
        return (f"Working ✓  ({r.provider} · {r.model} · {r.ms:.0f} ms) "
                f"answered: {r.answer.choice}")
