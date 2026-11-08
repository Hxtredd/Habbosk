"""Answer reliability (B4): one answer category for every question.

    r = brain.respond("Who maintains the Lantern Garden?", viewer=...)
    r.category   established | documented | ai_generated | unknown
    r.reason     unknown only: why (see model.UNKNOWN_REASONS)

Built on ask() (B3 reasoning) and search() (B2), which are NOT changed:

  ask() status           category
  established / absent   ESTABLISHED  - every claim used is verified
  unverified             DOCUMENTED   - only if EVERY unverified claim used has
                                        direct / confirmation evidence from a real
                                        source (not someone's own statement) whose
                                        quote, read on its own with B3's strict
                                        sentence forms, states exactly the same fact
                         UNKNOWN (unsupported) - otherwise; the possibility is
                                        shown, labelled as not confirmed
  missing                UNKNOWN (nothing_documented, or only_outdated when only
                                  claims marked outdated would answer it)
  conflicting            UNKNOWN (sources_disagree) - each side shown
  ambiguous              UNKNOWN (ambiguous) - SK asks which one is meant
  not_reasoned           UNKNOWN (not_understood) - search results are listed
                                  separately as labelled related records,
                                  never as the answer

AI-generated answers are never produced here (no AI gateway before B8).
Quiz memory and World Atlas records are related records only.

Read-only: respond() never writes, stores or changes anything; it only uses
read calls that apply the asker's visibility (ask, the visible records, and
store.cited_evidence / store.claim).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .facts import extract
from .model import Citation, Response
from .reason import FactIndex, answer as reason_answer, plan
from .retrieve import visible_library_docs
from .text import tokens

SUPPORT_KINDS = ("direct", "confirmation")

# -- language -------------------------------------------------------------------
# Conservative: Spanish only with an opening "¿" / "¡" or a Spanish question
# word first; everything else is answered in English.
_ES_FIRST = {"que", "quien", "quienes", "cual", "cuales", "cuanto", "cuanta", "cuantos",
             "cuantas", "donde", "adonde", "como", "cuando", "hay", "puedo", "puede",
             "tiene", "existe", "se", "numero", "codigo"}


def language(question: str) -> str:
    q = question or ""
    if "¿" in q or "¡" in q:
        return "es"
    words = tokens(q)
    return "es" if words and words[0] in _ES_FIRST else "en"


# -- wording (filled only from structured values) ---------------------------------
_T = {
    "en": {
        "established": "{value}.",
        "established_none": "There is none - this is documented.",
        "documented": "According to {source} (not reviewed): {value}.",
        "documented_none": "According to {source} (not reviewed): there is none.",
        "nothing_documented": "I don't know: nothing I can see documents this. "
                              "That does not mean there is none.",
        "sources_disagree": "I don't know: the records disagree ({options}).",
        "ambiguous": "I don't know which one you mean: {options}?",
        "clarify": "Which one do you mean?",
        "only_outdated": "I don't know: the only information is marked outdated "
                         "(it said: {possible}).",
        "unsupported": "I don't know: it has been stated ({possible}), "
                       "but no source confirms it.",
        "unsupported_bare": "I don't know: what is recorded is not confirmed by a source.",
        "not_understood": "I can't reason about this kind of question yet.",
        "none": "none",
    },
    "es": {
        "established": "{value}.",
        "established_none": "No hay ninguno - está documentado.",
        "documented": "Según {source} (sin revisar): {value}.",
        "documented_none": "Según {source} (sin revisar): no hay ninguno.",
        "nothing_documented": "No lo sé: nada de lo que puedo ver lo documenta. "
                              "Eso no significa que no exista.",
        "sources_disagree": "No lo sé: los registros no coinciden ({options}).",
        "ambiguous": "No sé a cuál te refieres: ¿{options}?",
        "clarify": "¿A cuál te refieres?",
        "only_outdated": "No lo sé: la única información está marcada como desactualizada "
                         "(decía: {possible}).",
        "unsupported": "No lo sé: se ha dicho ({possible}), pero ninguna fuente lo confirma.",
        "unsupported_bare": "No lo sé: lo registrado no está confirmado por ninguna fuente.",
        "not_understood": "Todavía no puedo razonar sobre este tipo de pregunta.",
        "none": "ninguno",
    },
}


def _say(lang: str, key: str, **kw) -> str:
    return _T[lang][key].format(**kw)


# -- the strict support rule ------------------------------------------------------
def _fact_key(f) -> Optional[Tuple]:
    return None if f is None else (f.relation, f.place, f.value, f.qualifier)


def quote_supports(claim_text: str, claim_subject: str, quote: str) -> bool:
    """True only if `quote`, read ON ITS OWN (no subject, no context) with B3's
    strict sentence forms, states exactly the fact the claim states. Anything
    else - another value, a negation, a hedge, a condition, a date, a run-on
    sentence, an unrecognised wording, an empty quote - is not support."""
    want = _fact_key(extract(claim_text or "", claim_subject or ""))
    if want is None or not (quote or "").strip():
        return False
    return _fact_key(extract(quote)) == want


def _qualifying(ev: dict) -> bool:
    return ev["kind"] in SUPPORT_KINDS and ev["source_id"] is not None \
        and ev["source_kind"] != "user_statement" and bool((ev["locator"] or "").strip())


# -- citations ----------------------------------------------------------------------
def _date(ev: dict) -> str:
    return (ev.get("captured_at") or ev.get("source_added") or "")[:10]


def _cite(store, claims: List[dict], viewer, check_support: bool) -> List[Citation]:
    out: List[Citation] = []
    seen = set()
    for c in claims:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        evs = store.cited_evidence(c["id"], viewer)
        cit = Citation(c["id"], c["text"], c["status"], c["scope"],
                       [{"kind": e["kind"], "quote": e["locator"] or "",
                         "source": e["title"] or "", "source_kind": e["source_kind"] or "",
                         "date": _date(e)} for e in evs])
        if check_support:
            full = store.claim(c["id"], viewer) or {}
            cit.supports = any(_qualifying(e) and quote_supports(c["text"], full.get("subject") or "",
                                                                 e["locator"])
                               for e in evs)
        out.append(cit)
    return out


def _source_of(citations: List[Citation]) -> str:
    for c in citations:
        if c.supports:
            for e in c.evidence:
                if e["kind"] in SUPPORT_KINDS and e["source"]:
                    return e["source"] + (f", {e['date']}" if e["date"] else "")
    return "a source"


# -- "only outdated" (never changes the category) ---------------------------------------
class _AsCurrent:
    """A record seen as if it were current, for the outdated check only."""
    __slots__ = ("_d", "status")

    def __init__(self, d):
        self._d = d
        self.status = "submitted" if d.status == "outdated" else d.status

    def __getattr__(self, name):
        return getattr(self._d, name)


def _outdated_possibility(store, question: str, viewer) -> Tuple[Optional[str], List[dict]]:
    p = plan(question)
    if p is None:
        return None, []
    docs = visible_library_docs(store, viewer)
    outdated = {int(d.record_id) for d in docs if d.status == "outdated"}
    if not outdated:
        return None, []
    r = reason_answer(p, FactIndex([_AsCurrent(d) for d in docs]))
    used = [s for st in r.steps for s in st.supports]
    if r.status not in ("established", "unverified", "absent") or \
            not any(s.claim_id in outdated for s in used):
        return None, []
    claims = [{"id": s.claim_id, "text": s.text,
               "status": "outdated" if s.claim_id in outdated else s.status, "scope": s.scope}
              for s in used]
    return (r.answer or r.possible), claims


# -- respond ------------------------------------------------------------------------
def _related(hits) -> List[dict]:
    return [{"text": h.text, "label": h.label, "status": h.status, "module": h.module}
            for h in hits]


def _all_claims(steps: List[dict]) -> List[dict]:
    return [c for st in steps for c in st["claims"]]


def _localise(value: Optional[str], lang: str) -> Optional[str]:
    """B3 writes a quest reward as "<n> coins"; in Spanish that is "<n> monedas"."""
    if value and lang == "es":
        m = re.fullmatch(r"(\d+) coins", value)
        if m:
            return f"{m.group(1)} monedas"
    return value


def respond(brain, question: str, viewer: Optional[str] = None) -> Response:
    a = brain.ask(question, viewer=viewer)
    store = brain.store
    lang = language(question)
    r = Response(question, lang, "unknown", steps=a.steps, related=_related(a.hits),
                 b3_status=a.status)
    last = a.steps[-1] if a.steps else {}
    absent = last.get("outcome") == "absent"

    if a.status in ("established", "absent"):
        r.category, r.audience = "established", a.audience
        r.citations = _cite(store, _all_claims(a.steps), viewer, False)
        r.value = _say(lang, "none") if absent else _localise(a.answer, lang)
        r.sentence = _say(lang, "established_none") if absent else \
            _say(lang, "established", value=r.value)
        return r

    if a.status == "unverified":
        weak = [st for st in a.steps if not st["verified"]]
        r.citations = _cite(store, _all_claims(a.steps), viewer, True)
        ok = {c.claim_id: c.supports for c in r.citations}
        documented = bool(weak) and all(st["claims"] and all(ok.get(c["id"]) for c in st["claims"])
                                        for st in weak)
        if documented:
            r.category, r.audience = "documented", a.audience
            r.value = _say(lang, "none") if absent else _localise(a.possible, lang)
            src = _source_of(r.citations)
            r.sentence = _say(lang, "documented_none", source=src) if absent else \
                _say(lang, "documented", source=src, value=r.value)
            return r
        r.reason, r.possible = "unsupported", _localise(a.possible, lang)
        r.sentence = _say(lang, "unsupported", possible=r.possible) if r.possible else \
            _say(lang, "unsupported_bare")
        return r

    if a.status == "missing":
        possible, claims = _outdated_possibility(store, question, viewer)
        if possible:
            possible = _localise(possible, lang)
            r.reason, r.possible = "only_outdated", possible
            r.citations = _cite(store, claims, viewer, False)
            r.sentence = _say(lang, "only_outdated", possible=possible)
        else:
            r.reason = "nothing_documented"
            r.sentence = _say(lang, "nothing_documented")
        return r

    if a.status == "conflicting":
        r.reason = "sources_disagree"
        r.citations = _cite(store, last.get("claims", []), viewer, False)
        r.sides = [{"value": v} for v in last.get("candidates", [])]
        r.sentence = _say(lang, "sources_disagree", options=" / ".join(last.get("candidates", [])))
        return r

    if a.status == "ambiguous":
        opts = list(last.get("candidates", []))
        r.reason = "ambiguous"
        r.clarification = {"question": _say(lang, "clarify"), "options": opts}
        r.citations = _cite(store, last.get("claims", []), viewer, False)
        r.sentence = _say(lang, "ambiguous", options=" / ".join(opts))
        return r

    r.reason = "not_understood"
    r.sentence = _say(lang, "not_understood")
    return r
