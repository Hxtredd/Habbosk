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

B5.1 question understanding (understand.py), ONLY when B3's exact grammar
did not understand the question, or understood it with a name the asker
does not know - so every question B3 answered is answered exactly as before:
  understood        the same reasoning and categories; the answer says how the
                    question was read (`understood_as`, `interpretation`)
  a misspelt name   UNKNOWN (did_you_mean) - "Did you mean the X?" with a
                    question to tap; never an answer, never evidence
  several names /   UNKNOWN (ambiguous) - SK asks which one, each choice a
  two meanings      question it understands exactly
  anything else     exactly as before (not understood / nothing documented)
The reply language is scored (understand.language), with the words of the
names the asker may see left out.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .facts import extract
from .model import Citation, Response
from .reason import ANYWHERE, Ask
from .reason import answer as reason_answer
from .retrieve import visible_library_docs
from . import understand_words as W
from .text import tokens
from .understand import about, question_for, understand

SUPPORT_KINDS = ("direct", "confirmation")

# -- language -------------------------------------------------------------------
# B5.1: scored, not first-word (understand.language): an opening "¿" / "¡",
# "ñ" and Spanish words against English words; words inside a name the asker
# may see are not counted. Understanding works in both languages either way.
from .understand import language  # noqa: E402,F401


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
        "evidence_withdrawn": "I don't know: a source behind this was withdrawn by its owner, "
                              "so it is awaiting review (it said: {possible}).",
        "source_changed": "I don't know for sure: the source behind this has changed since it "
                          "was quoted, so it is awaiting a new check (it said: {possible}).",
        "passage_disagrees": "I don't know: a source says otherwise ({options}).",
        "not_understood": "I can't reason about this kind of question yet.",
        "did_you_mean": "Did you mean {name}?",
        "relation_choice": "Do you mean {options}?",
        "understood_as": "Understood as: {about}.",
        "read_as": "\"{a}\" read as \"{b}\"",
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
        "evidence_withdrawn": "No lo sé: su propietario retiró una fuente en la que se basaba; "
                              "está pendiente de revisión (decía: {possible}).",
        "source_changed": "No lo sé con seguridad: la fuente cambió desde que se citó; está "
                          "pendiente de una nueva comprobación (decía: {possible}).",
        "passage_disagrees": "No lo sé: una fuente dice otra cosa ({options}).",
        "not_understood": "Todavía no puedo razonar sobre este tipo de pregunta.",
        "did_you_mean": "¿Te refieres a {name}?",
        "relation_choice": "¿Te refieres a {options}?",
        "understood_as": "Entendido como: {about}.",
        "read_as": "\"{a}\" leído como \"{b}\"",
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


def _supports(store, e: dict, c: dict, full: dict) -> bool:
    """Legacy evidence: the B4 rule. A quotation from an imported source (B6):
    exact, current, inside one sentence / list item, not flagged, not self-written
    or AI-generated - and the quote AND its whole passage state the fact."""
    from . import sources as B6
    if not _qualifying(e):
        return False
    if B6.is_imported(store, e["source_id"]):
        return B6.evidence_check(store, e["evidence_id"], {"text": c["text"],
                                                           "subject": full.get("subject") or ""},
                                 strict=True)[0]
    return quote_supports(c["text"], full.get("subject") or "", e["locator"])


# -- citations ----------------------------------------------------------------------
def _date(ev: dict) -> str:
    """The source's own date only (B6: never when it was added or copied)."""
    return (ev.get("captured_at") or "")[:10]


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
                         "date": _date(e), "page": e.get("page"),
                         "withheld": bool(e.get("withheld"))} for e in evs])
        if check_support:
            full = store.claim(c["id"], viewer) or {}
            cit.supports = any(_supports(store, e, c, full) for e in evs)
        out.append(cit)
    return out


def _personal_details(store, claims, viewer) -> bool:
    """B6.1: does any citation show source details (title, date) from the viewer's OWN
    personal file although the claim is shared? Then the response is the viewer's alone."""
    return any(e.get("personal_source") for c in claims
               for e in store.cited_evidence(c["id"], viewer))


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


def _outdated_possibility(brain, p, viewer) -> Tuple[Optional[str], List[dict]]:
    if p is None:
        return None, []
    docs = visible_library_docs(brain.store, viewer)
    outdated = {int(d.record_id) for d in docs if d.status == "outdated"}
    if not outdated:
        return None, []
    r = reason_answer(p, brain._fact_index(viewer, [_AsCurrent(d) for d in docs]))
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


class _Context:
    """What understanding needs for one asker, read once and only if needed."""

    def __init__(self, brain, viewer):
        self.brain, self.viewer = brain, viewer
        self._names = self._vocab = None

    @property
    def names(self):
        if self._names is None:
            self._names = self.brain._known_names(self.viewer)
        return self._names

    @property
    def vocab(self):
        if self._vocab is None:
            self._vocab = self.brain._vocab()
        return self._vocab


def _single_name(p) -> Optional[str]:
    """The place name of a one-step B3 plan ("who maintains <name>"), else None."""
    if isinstance(p, Ask) and not p.inverse and isinstance(p.place, str) and p.place != ANYWHERE:
        return p.place
    return None


def _language(question: str, ctx: _Context) -> str:
    """The reply language. Spanish words may be part of a name ("Sala Azul",
    "Faro del Norte"): whenever a Spanish word is counted, the words of the
    names the asker may see are left out and the question is scored again."""
    words = set(tokens(question))
    if words & W.ES:
        return language(question, ctx.names)
    return language(question)


def _final_fact(store, steps: List[dict], viewer) -> Optional[dict]:
    """The fact (B3's built-in reading) of the claims behind the answer's last
    step - read through the viewer-checked store.claim()."""
    last = steps[-1] if steps else {}
    for c in last.get("claims", []):
        full = store.claim(c["id"], viewer)
        f = extract(full["text"], full.get("subject") or "") if full else None
        if f is not None:
            return {"relation": f.relation, "place": f.place, "value": f.value,
                    "absent": f.absent, "qualifier": f.qualifier}
    return None


def _challenge(brain, r: Response, viewer) -> Response:
    """B6: passages that state another value. Established: shown alongside, the
    category never changes. Documented: the sources disagree."""
    from . import sources as B6
    if r.category not in ("established", "documented"):
        return r
    store = brain.store
    fact = _final_fact(store, r.steps, viewer)
    if fact is None:
        return r
    ch = B6.challenges(store, fact, viewer)
    if not ch:
        return r
    r.challenges = ch
    if r.category == "documented":
        vals = [v if v is not None else _say(r.language, "none") for g in ch for v in g["values"]]
        r.sides = [{"value": r.value, "from": "claims"}] + \
            [{"value": v, "from": "passage", "group": g["publisher"] or "unnamed material",
              "scope": g["scope"]} for g in ch for v in g["values"]]
        r.category, r.reason, r.possible = "unknown", "sources_disagree", r.value
        r.value = None
        r.sentence = _say(r.language, "passage_disagrees",
                          options=" / ".join([r.possible] + vals))
        if any(g["scope"] == "personal" for g in ch):
            r.audience = "personal"
    return r


def respond(brain, question: str, viewer: Optional[str] = None) -> Response:
    r = _respond(brain, question, viewer)
    return _challenge(brain, r, viewer)


def _respond(brain, question: str, viewer: Optional[str] = None) -> Response:
    a = brain.ask(question, viewer=viewer)
    ctx = _Context(brain, viewer)
    lang = _language(question, ctx)
    p = brain._plan(question)
    u = None
    # B5.1: only when B3's exact grammar did not understand the question, or
    # understood it with a name the asker does not know
    if a.status == "not_reasoned" or (a.status == "missing" and _single_name(p) is not None
                                      and _single_name(p) not in ctx.names):
        u = understand(question, ctx.names, ctx.vocab)
    if u is None:
        r = _categorise(brain, a, p, lang, viewer, question)
        if r.reason == "ambiguous" and _single_name(p) is not None and r.clarification:
            r.clarification["questions"] = [question_for(p.relation, o, lang, ctx.vocab,
                                                         p.qualifier)
                                            for o in r.clarification["options"]]
        return r
    return _understood(brain, a, u, lang, viewer, question, ctx)


def _understood(brain, a, u, lang, viewer, question, ctx) -> Response:
    """The answer to a question understood by B5.1 (understand.py)."""
    vocab = ctx.vocab
    interp = u.as_dict()
    if u.kind == "plan":
        b = brain._reasoned(question, u.ask(), viewer, a.hits)
        r = _categorise(brain, b, u.ask(), lang, viewer, question)
        r.b3_status = a.status
        text = _say(lang, "understood_as", about=about(u.relation, u.shown, lang, vocab,
                                                         u.qualifier, u.inverse))
        if u.corrections:
            text = text[:-1] + " (" + ", ".join(_say(lang, "read_as", a=x, b=y)
                                                for x, y in u.corrections) + ")."
        r.understood_as, r.interpretation = text, interp
        # the choices are places: a question about each (not for a reverse question,
        # where several places fit one person - each is a possible answer, not a choice)
        if r.reason == "ambiguous" and r.clarification and not u.inverse:
            r.clarification["questions"] = [question_for(u.relation, o, lang, vocab, u.qualifier)
                                            for o in r.clarification["options"]]
        return r
    r = Response(question, lang, "unknown", related=_related(a.hits), b3_status=a.status,
                 interpretation=interp, audience="personal")
    if u.kind == "did_you_mean":
        name = u.shown if (u.inverse or lang == "es") else f"the {u.shown}"
        r.reason = "did_you_mean"
        r.sentence = _say(lang, "did_you_mean", name=name)
        r.clarification = {"question": r.sentence, "options": [u.shown],
                           "questions": [question_for(u.relation, u.shown, lang, vocab,
                                                      u.qualifier, u.inverse)]}
        return r
    if u.kind == "ambiguous_name":
        opts = [s for _, s in u.candidates]
        r.reason = "ambiguous"
        r.sentence = _say(lang, "ambiguous", options=" / ".join(opts))
        r.clarification = {"question": _say(lang, "clarify"), "options": opts,
                           "questions": [question_for(u.relation, o, lang, vocab, u.qualifier,
                                                      u.inverse) for o in opts]}
        return r
    # ambiguous_relation: one word that can mean two things ("in charge")
    opts = [about(rel, u.shown, lang, vocab, u.qualifier, u.inverse) for rel in u.relations]
    r.reason = "ambiguous"
    r.sentence = _say(lang, "relation_choice", options=" / ".join(opts))
    r.clarification = {"question": _say(lang, "clarify"), "options": opts,
                       "questions": [question_for(rel, u.shown, lang, vocab, u.qualifier,
                                                  u.inverse) for rel in u.relations]}
    return r


def _categorise(brain, a, p, lang, viewer, question) -> Response:
    """B4: one category for ask()'s result (unchanged rules)."""
    store = brain.store
    r = Response(question, lang, "unknown", steps=a.steps, related=_related(a.hits),
                 b3_status=a.status)
    last = a.steps[-1] if a.steps else {}
    absent = last.get("outcome") == "absent"

    if a.status in ("established", "absent"):
        r.category, r.audience = "established", a.audience
        r.citations = _cite(store, _all_claims(a.steps), viewer, False)
        if _personal_details(store, _all_claims(a.steps), viewer):
            r.audience = "personal"
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
            if _personal_details(store, _all_claims(a.steps), viewer):
                r.audience = "personal"
            r.value = _say(lang, "none") if absent else _localise(a.possible, lang)
            src = _source_of(r.citations)
            r.sentence = _say(lang, "documented_none", source=src) if absent else \
                _say(lang, "documented", source=src, value=r.value)
            return r
        r.reason, r.possible = "unsupported", _localise(a.possible, lang)
        r.sentence = _say(lang, "unsupported", possible=r.possible) if r.possible else \
            _say(lang, "unsupported_bare")
        # B5.2: verified shared knowledge whose last support its owner withdrew
        if r.possible and any((store.claim(c["id"], viewer) or {}).get("withdrawn_support")
                              for c in _all_claims(a.steps)):
            r.reason = "evidence_withdrawn"
            r.sentence = _say(lang, "evidence_withdrawn", possible=r.possible)
        # B6: the quoted source has a newer version (or the quoted one was purged)
        elif r.possible and any((store.claim(c["id"], viewer) or {}).get("source_changed")
                                for c in _all_claims(a.steps)):
            r.reason = "source_changed"
            r.sentence = _say(lang, "source_changed", possible=r.possible)
        return r

    if a.status == "missing":
        possible, claims = _outdated_possibility(brain, p, viewer)
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
