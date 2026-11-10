"""B6.2: Response.shareable() / BrainAnswer.shareable() - a sanitized COPY of a response
that is safe to pass on. This module only PREPARES a response; nothing here shares,
sends, stores or publishes anything (there is no sharing feature).

Design: an allow-list, not a block-list. The copy is built field by field from values that
are checked one by one; anything unknown, of an unexpected type, or not demonstrably shared
is either left out or - when it contradicts the response's own audience - makes the whole
call fail with NotShareable ("fail closed"). The original object is never modified (it is
read, and every kept value is copied)."""
from __future__ import annotations

import copy
import functools
import re
import unicodedata
from typing import Any, List


class NotShareable(ValueError):
    """The response cannot be established as safe to pass on."""


_OUTCOMES = {"value", "absent", "missing", "conflicting", "ambiguous", ""}
_CLAIM_STATUSES = None          # filled from model.STATUSES on first use


def _bad(why: str):
    raise NotShareable(why)


class Residue:
    """What was removed: `names` (source titles, publishers) and `texts` (passage text...)."""
    def __init__(self):
        self.names: List[str] = []
        self.texts: List[str] = []
        self.shared_words: set = set()          # words of shared claims' own subjects


def _statuses():
    from .model import STATUSES
    return set(STATUSES)


def _int(v: Any, what: str) -> int:
    if type(v) is not int:
        _bad(f"{what}: expected a number")
    return v


def _page(v: Any):
    return v if type(v) is int else None


def _norm(t: str) -> str:
    """Comparison form: accents, case, quote styles, dashes and spacing do not hide a match."""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"[\u2018\u2019\u201a\u201b\u2032`]", "'", t)
    t = re.sub(r"[\u201c\u201d\u201e\u201f\u2033]", '"', t)
    t = re.sub(r"[\u2010-\u2015\u2212]", "-", t)
    return re.sub(r"\s+", " ", t).strip()


def _str(v: Any, what: str) -> str:
    if not isinstance(v, str):
        _bad(f"{what}: expected text")
    return v


def _opt_str(v: Any, what: str):
    return None if v is None else _str(v, what)


def _shared_claim(c: Any, what: str) -> dict:
    """A claim entry of a step: its scope must be 'shared' (it would contradict the audience)."""
    if not isinstance(c, dict) or c.get("scope") != "shared":
        _bad(f"{what}: not a shared claim")
    return {"id": _int(c.get("id"), what + " id"), "text": _str(c.get("text"), what + " text"),
            "status": _one_of(c.get("status"), _statuses(), what + " status"), "scope": "shared"}


def _step(st: Any, residue: "Residue") -> dict:
    if not isinstance(st, dict):
        _bad("step: not a mapping")
    out = {"ask": _str(st.get("ask", ""), "step ask"),
           "outcome": _one_of(st.get("outcome", ""), _OUTCOMES, "step outcome"),
           "value": _opt_str(st.get("value"), "step value"),
           "verified": bool(st.get("verified")),
           # names of other candidate places can only exist in an ambiguous (never shared)
           # answer; they are never carried over
           "candidates": [],
           "claims": [_shared_claim(c, "step claim") for c in (st.get("claims") or [])],
           "also_unverified": [_shared_claim(c, "step also_unverified")
                               for c in (st.get("also_unverified") or [])]}
    for k, v in st.items():                       # whatever else a step carries is not copied
        if k not in out:
            residue.texts += _strings(v)
    return out


def _strings(v: Any) -> List[str]:
    if isinstance(v, str):
        return [v] if len(v.strip()) >= 3 else []
    if isinstance(v, dict):
        return [t for x in v.values() for t in _strings(x)]
    if isinstance(v, (list, tuple, set)):
        return [t for x in v for t in _strings(x)]
    return []


def _citation(c, residue: "Residue"):
    from .model import Citation
    if not isinstance(c, Citation):
        _bad("citation: not a Citation")
    if c.scope != "shared":
        _bad("citation: not a shared claim")
    ev = []
    for e in c.evidence or []:
        if not isinstance(e, dict):
            _bad("citation evidence: not a mapping")
        if e.get("personal_details") is False:     # explicitly established as not personal
            ev.append({"kind": _str(e.get("kind", ""), "evidence kind"),
                       "quote": _str(e.get("quote", ""), "evidence quote"),
                       "source": _str(e.get("source", ""), "evidence source"),
                       "source_kind": _str(e.get("source_kind", ""), "evidence source kind"),
                       "date": _str(e.get("date", ""), "evidence date"),
                       "page": _page(e.get("page")),
                       "withheld": bool(e.get("withheld")),
                       "personal_details": False})
        else:                                      # personal, or not established: left out
            residue.names += _strings(e.get("source"))   # a title a sentence may name
    residue.shared_words |= set(_words(_str(c.subject, "citation subject")))   # vouches only
    return Citation(_int(c.claim_id, "citation id"), _str(c.text, "citation text"),
                    _one_of(c.status, _statuses(), "citation status"),
                    "shared", ev, bool(c.supports))


def _challenge(g: Any, residue: "Residue"):
    if not isinstance(g, dict):
        _bad("challenge: not a mapping")
    if g.get("scope") != "shared":
        exs = [e for e in g.get("examples") or [] if isinstance(e, dict)]
        residue.names += _strings([g.get("publisher"), [e.get("source") for e in exs]])
        residue.texts += _strings([e.get("text") for e in exs])
        return None
    vals = g.get("values") or []
    ex = g.get("examples") or []
    # publisher, labels (author / publisher names) and example source titles are not carried:
    # nothing here establishes that they are the shared copy's own granted details
    residue.names += _strings([g.get("publisher"), g.get("labels")] +
                              [e.get("source") for e in ex if isinstance(e, dict)])
    return {"values": [_opt_str(v, "challenge value") for v in vals],
            "passages": _int(g.get("passages", 0), "challenge passages"),
            "sources": _int(g.get("sources", 0), "challenge sources"),
            "scope": "shared", "internal": bool(g.get("internal")),
            "examples": [{"text": _str(e.get("text"), "challenge example"),
                          "page": _page(e.get("page"))}
                         for e in ex if isinstance(e, dict)],
            "counts_as": _str(g.get("counts_as", ""), "challenge counts_as")}


def _side(s: Any, residue: "Residue"):
    if not isinstance(s, dict):
        _bad("side: not a mapping")
    if s.get("from") == "claims":
        return {"value": _opt_str(s.get("value"), "side value"), "from": "claims"}
    if s.get("from") == "passage" and s.get("scope") == "shared":
        residue.names += _strings(s.get("group"))
        return {"value": _opt_str(s.get("value"), "side value"), "from": "passage",
                "scope": "shared"}
    residue.names += _strings(s.get("group"))
    return None


_LABEL_WORDS = ("the room whose caretaker is runner who looks after it runs its where leads to "
                "exit no place named only one documented is side called room id quest reward "
                "coins monedas")


def _words(text: str) -> List[str]:
    return re.findall(r"[^\W_]+", _norm(text))


def _template_words() -> set:
    """Every word of the fixed answer wording and step labels (both languages)."""
    from .respond import _T
    from . import reason
    words = set(_words(_LABEL_WORDS))
    for lang in _T.values():
        for t in lang.values():
            words |= set(_words(re.sub(r"\{[^}]*\}", " ", t)))
    for v in list(reason._LABEL.values()) + list(reason._DIRS.values()):
        words |= set(_words(v))
    return words


def _check_provenance(value, possible, sentence, steps, cits, ch, sides, extra_words=()):
    """A name in a step label, the value, the possible value or the sentence must come from the
    shared material the response itself carries (claim texts, kept evidence, kept passages) or
    from the fixed wording. A label is built from names the ASKER typed or that her own aliases
    map to; this makes sure none of them is in a response passed on. Fail closed: a word that
    cannot be found raises NotShareable."""
    corpus = set(extra_words)
    for st in steps:
        for c in st["claims"] + st["also_unverified"]:
            corpus |= set(_words(c["text"]))
    for c in cits:
        corpus |= set(_words(c.text))
        for e in c.evidence:
            corpus |= set(_words(e["quote"]) + _words(e["source"]) + _words(e["date"]))
    for g in ch:
        for v in g["values"]:
            corpus |= set(_words(v or ""))
        for e in g["examples"]:
            corpus |= set(_words(e["text"]))
    for x in sides:
        corpus |= set(_words(x["value"] or ""))
    fixed = _template_words()
    for st in steps:
        for w in _words(st["ask"]):
            if w not in corpus and w not in fixed:
                _bad("a step label holds a name not found in the shared material")
        for w in _words(st["value"] or ""):
            if w not in corpus and w not in fixed:
                _bad("a step value holds a name not found in the shared material")
    for what, text in (("value", value), ("possible", possible), ("sentence", sentence)):
        for w in _words(text or ""):
            if w not in corpus and w not in fixed:
                _bad(f"the {what} holds a word not found in the shared material")


def _check_residue(names: List[str], texts: List[str], kept: List[str]):
    """A string removed above (a source title, a personal passage, a publisher...) must not
    survive inside text that was kept (e.g. a ready-made sentence naming the source). Names
    (titles, publishers) also count when three or more of their words appear in a row."""
    low = [_norm(k) for k in kept]
    for t in texts:
        t = _norm(t)
        if t and any(t in k for k in low):
            _bad("a removed detail still appears in kept text")
    for n in names:
        n = _norm(n)
        if not n:
            continue
        w = n.split(" ")
        windows = [n] + [" ".join(w[i:i + 3]) for i in range(len(w) - 2)] if len(w) >= 3 else [n]
        if any(x in k for x in windows for k in low):
            _bad("a removed name still appears in kept text")


_KNOWN_RESPONSE_FIELDS = {"question", "language", "category", "reason", "value", "sentence",
                          "citations", "steps", "clarification", "sides", "possible", "related",
                          "audience", "ai", "save_offer", "b3_status", "understood_as",
                          "challenges", "interpretation"}
_KNOWN_ANSWER_FIELDS = {"question", "answered", "answer", "basis", "confidence", "hits", "trace",
                        "status", "possible", "steps", "audience"}
_STATUSES = {"established", "absent", "unverified", "missing", "conflicting", "ambiguous",
             "not_reasoned"}
_BASES = {"documented", "derived", "insufficient", ""}
_CONFIDENCES = {"verified", "unverified", "none"}


def _unknown_fields(obj, known):
    import dataclasses
    for f in dataclasses.fields(obj):              # a field this version does not know: closed
        if f.name not in known:
            _bad(f"unknown field {f.name!r}")


def _one_of(v: Any, allowed, what: str):
    if not isinstance(v, str) or v not in allowed:
        _bad(f"{what}: not an allowed value")
    return v


def _guarded(fn):
    """Anything unexpected while reading a response closes the door with NotShareable."""
    @functools.wraps(fn)
    def run(x):
        try:
            return fn(x)
        except NotShareable:
            raise
        except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:
            raise NotShareable(f"cannot be established as safe ({type(e).__name__})") from None
    return run


@_guarded
def sanitize_response(r):
    import dataclasses
    from .model import CATEGORIES, UNKNOWN_REASONS, Response
    if not isinstance(r, Response):
        _bad("not a Response")
    if r.audience != "shared":
        _bad("only a response marked audience='shared' can be passed on")
    if r.save_offer is not None:
        _bad("save_offer is not part of a shareable response")
    _unknown_fields(r, _KNOWN_RESPONSE_FIELDS)
    _one_of(r.category, CATEGORIES, "category")
    _one_of(r.reason, set(UNKNOWN_REASONS) | {""}, "reason")
    _one_of(r.language, {"en", "es"}, "language")
    _one_of(r.ai, {"none"}, "ai")
    _opt_str(r.value, "value")
    _opt_str(r.possible, "possible")
    _str(r.sentence, "sentence")
    src = copy.deepcopy(r)                         # everything below reads the private copy
    residue = Residue()
    steps = [_step(s, residue) for s in src.steps]
    cits = [_citation(c, residue) for c in src.citations]
    ch = [x for x in (_challenge(g, residue) for g in src.challenges) if x is not None]
    sides = [x for x in (_side(s, residue) for s in src.sides) if x is not None]
    # b3_status says how the reasoning went ("not_reasoned" / "missing" reveal that a typo
    # had to be corrected), so it is not carried
    out = dataclasses.replace(
        src, question="", related=[], understood_as="", interpretation=None, clarification=None,
        b3_status="", steps=steps, citations=cits, challenges=ch, sides=sides)
    kept = [out.sentence, out.value or "", out.possible or ""] + \
        _strings([steps, [dataclasses.asdict(c) for c in cits], ch, sides])
    # wording the asker typed (question, understood_as, related) overlaps with shared words by
    # nature and is not scanned; titles, publishers and passages of removed material are
    _check_residue(residue.names, residue.texts, kept)
    _check_provenance(out.value, out.possible, out.sentence, steps, cits, ch, sides,
                      residue.shared_words)
    return out


@_guarded
def sanitize_answer(a):
    import dataclasses
    from .model import BrainAnswer, Hit
    if not isinstance(a, BrainAnswer):
        _bad("not a BrainAnswer")
    if a.audience != "shared":
        _bad("only an answer marked audience='shared' can be passed on")
    _unknown_fields(a, _KNOWN_ANSWER_FIELDS)
    _one_of(a.status, _STATUSES, "status")
    _one_of(a.basis, _BASES, "basis")
    _one_of(a.confidence, _CONFIDENCES, "confidence")
    _opt_str(a.answer, "answer")
    _opt_str(a.possible, "possible")
    src = copy.deepcopy(a)
    residue = Residue()
    steps = [_step(s, residue) for s in src.steps]
    hits = []
    for h in src.hits:                             # only a SHARED library claim is kept
        if not isinstance(h, Hit):
            _bad("hit: not a Hit")
        if h.module == "library" and isinstance(h.detail, dict) and \
                h.detail.get("scope") == "shared":
            # the score is computed from the asker's question and corpus: not carried
            from .model import ORIGINS
            hits.append(Hit(h.module, _str(h.record_id, "hit id"), _str(h.text, "hit text"),
                            _one_of(h.status, _statuses(), "hit status"),
                            _one_of(h.origin, set(ORIGINS), "hit origin"), 0.0,
                            _str(h.category, "hit category"), [], {"scope": "shared"}))
    out = dataclasses.replace(src, question="", trace=[], hits=hits, steps=steps)
    kept = [out.answer or "", out.possible or ""] + _strings([steps, [h.text for h in hits]])
    _check_residue(residue.names, residue.texts, kept)
    _check_provenance(out.answer, out.possible, "", steps, [], [], [], residue.shared_words)
    return out
