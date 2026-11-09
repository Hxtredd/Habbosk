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
from typing import Any, List


class NotShareable(ValueError):
    """The response cannot be established as safe to pass on."""


def _bad(why: str):
    raise NotShareable(why)


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
    return {"id": c.get("id"), "text": _str(c.get("text"), what + " text"),
            "status": _str(c.get("status"), what + " status"), "scope": "shared"}


def _step(st: Any, residue: List[str]) -> dict:
    if not isinstance(st, dict):
        _bad("step: not a mapping")
    out = {"ask": _str(st.get("ask", ""), "step ask"),
           "outcome": _str(st.get("outcome", ""), "step outcome"),
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
            residue += _strings(v)
    return out


def _strings(v: Any) -> List[str]:
    if isinstance(v, str):
        return [v] if len(v.strip()) >= 3 else []
    if isinstance(v, dict):
        return [t for x in v.values() for t in _strings(x)]
    if isinstance(v, (list, tuple, set)):
        return [t for x in v for t in _strings(x)]
    return []


def _citation(c, residue: List[str]):
    from .model import Citation
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
                       "page": e.get("page") if isinstance(e.get("page"), int) else None,
                       "withheld": bool(e.get("withheld")),
                       "personal_details": False})
        else:                                      # personal, or not established: left out
            residue += _strings(e.get("source"))      # a title a ready-made sentence may name
    return Citation(int(c.claim_id), _str(c.text, "citation text"), _str(c.status, "status"),
                    "shared", ev, bool(c.supports))


def _challenge(g: Any, residue: List[str]):
    if not isinstance(g, dict):
        _bad("challenge: not a mapping")
    if g.get("scope") != "shared":
        residue += _strings([g.get("publisher"), [e.get("text") for e in g.get("examples") or []
                                                  if isinstance(e, dict)],
                             [e.get("source") for e in g.get("examples") or []
                              if isinstance(e, dict)]])
        return None
    vals = g.get("values") or []
    ex = g.get("examples") or []
    # publisher, labels (author / publisher names) and example source titles are not carried:
    # nothing here establishes that they are the shared copy's own granted details
    residue += _strings([g.get("publisher"), g.get("labels")] + [e.get("source") for e in ex
                                                                if isinstance(e, dict)])
    return {"values": [_opt_str(v, "challenge value") for v in vals],
            "passages": int(g.get("passages") or 0), "sources": int(g.get("sources") or 0),
            "scope": "shared", "internal": bool(g.get("internal")),
            "examples": [{"text": _str(e.get("text"), "challenge example"),
                          "page": e.get("page") if isinstance(e.get("page"), int) else None}
                         for e in ex if isinstance(e, dict)],
            "counts_as": _str(g.get("counts_as", ""), "challenge counts_as")}


def _side(s: Any, residue: List[str]):
    if not isinstance(s, dict):
        _bad("side: not a mapping")
    if s.get("from") == "claims":
        return {"value": _opt_str(s.get("value"), "side value"), "from": "claims"}
    if s.get("from") == "passage" and s.get("scope") == "shared":
        residue += _strings(s.get("group"))
        return {"value": _opt_str(s.get("value"), "side value"), "from": "passage",
                "scope": "shared"}
    residue += _strings(s.get("group"))
    return None


def _check_residue(residue: List[str], kept: List[str]):
    """A string removed above (a source title, a personal passage, a publisher...) must not
    survive inside text that was kept (e.g. a ready-made sentence naming the source)."""
    low = [k.lower() for k in kept]
    for r in residue:
        r = r.strip().lower()
        if r and any(r in k for k in low):
            _bad("a removed detail still appears in kept text")


def sanitize_response(r):
    import dataclasses
    from .model import Response
    if not isinstance(r, Response):
        _bad("not a Response")
    if r.audience != "shared":
        _bad("only a response marked audience='shared' can be passed on")
    if r.save_offer is not None:
        _bad("save_offer is not part of a shareable response")
    for f in dataclasses.fields(r):                # a field this version does not know: closed
        if f.name not in _KNOWN_RESPONSE_FIELDS:
            _bad(f"unknown response field {f.name!r}")
    src = copy.deepcopy(r)                         # everything below reads the private copy
    residue: List[str] = []
    steps = [_step(s, residue) for s in src.steps]
    cits = [_citation(c, residue) for c in src.citations]
    ch = [x for x in (_challenge(g, residue) for g in src.challenges) if x is not None]
    sides = [x for x in (_side(s, residue) for s in src.sides) if x is not None]
    residue += _strings([src.understood_as, src.interpretation, src.clarification])
    out = dataclasses.replace(
        src, question="", related=[], understood_as="", interpretation=None, clarification=None,
        steps=steps, citations=cits, challenges=ch, sides=sides)
    kept = [out.sentence, out.value or "", out.possible or ""] + \
        _strings([steps, cits and [dataclasses.asdict(c) for c in cits], ch, sides])
    # only removed details that name a PERSON'S material count here; wording the asker typed
    # (question, understood_as, related) overlaps with shared words by nature and is not scanned
    scan = [x for x in residue if x not in _strings([src.understood_as, src.interpretation,
                                                      src.clarification])]
    _check_residue(scan, kept)
    return out


_KNOWN_RESPONSE_FIELDS = {"question", "language", "category", "reason", "value", "sentence",
                          "citations", "steps", "clarification", "sides", "possible", "related",
                          "audience", "ai", "save_offer", "b3_status", "understood_as",
                          "challenges", "interpretation"}


def sanitize_answer(a):
    import dataclasses
    from .model import BrainAnswer, Hit
    if not isinstance(a, BrainAnswer):
        _bad("not a BrainAnswer")
    if a.audience != "shared":
        _bad("only an answer marked audience='shared' can be passed on")
    src = copy.deepcopy(a)
    residue: List[str] = []
    steps = [_step(s, residue) for s in src.steps]
    hits = []
    for h in src.hits:                             # only a SHARED library claim is kept
        if isinstance(h, Hit) and h.module == "library" and \
                isinstance(h.detail, dict) and h.detail.get("scope") == "shared":
            hits.append(Hit(h.module, h.record_id, _str(h.text, "hit text"), h.status, h.origin,
                            h.score, h.category, [], {"scope": "shared"}))
    return dataclasses.replace(src, question="", trace=[], hits=hits, steps=steps)
