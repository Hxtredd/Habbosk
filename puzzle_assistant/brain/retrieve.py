"""B2 retrieval: find and rank knowledge for a question.

Privacy: everything that can influence a viewer's results is computed from
what that viewer may see - candidate claims, word rarity (IDF), known
subject names, duplicates and contributor counts, conflict partners, and
explanations. The quiz memory and World Atlas belong to the local user, so
they only take part for the local user.

Equivalence: the FTS5 path only pre-selects candidates (claims containing
at least one query term); the plain-Python path applies the same test to
the same stored terms. Scoring is the same code for both.

Trust: ranking never changes a status. Verified knowledge gets a small
bonus only; relevance stays the main factor. Rejected claims are left out
unless asked for, outdated ones are ranked last, conflicting ones are never
hidden and are shown together with the claims they contradict (even beyond
the limit). Repeated identical claims are merged and gain nothing.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple

from .lexicon import SYNONYM_WEIGHT, synonyms
from .model import Hit
from .query import INTENT_EVIDENCE, Entity, Query, analyze, make_entities
from .text import index_text, normalize, polarity, stem, terms

MIN_RELEVANCE = 0.3          # below this a record is not shown at all
ANSWER_RELEVANCE = 0.5       # needed (with subject + intent fit) to count as matching the question
TRUST_BONUS = {"verified": 0.06, "awaiting_verification": 0.02}
_INTENT_STEMS = {k: {stem(w) for w in v} for k, v in INTENT_EVIDENCE.items()}


class Doc:
    __slots__ = ("module", "record_id", "text", "subject", "status", "origin", "category",
                 "sources", "detail", "terms", "norm", "owner", "positive", "subject_norm")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _library_docs(store, rows: List[dict]) -> List[Doc]:
    out = []
    for r in rows:
        out.append(Doc(module="library", record_id=str(r["id"]), text=r["text"],
                       subject=r["subject"] or "", status=r["status"], origin=r["origin"],
                       category=r["category"] or "", sources=None,
                       detail={"version": r["version"], "scope": r["scope"]},
                       terms=set(r["terms"].split()), norm=" " + r["norm"] + " ",
                       owner=r["owner_id"], positive=bool(r["positive"]),
                       subject_norm=r["subject_norm"]))
    return out


def _adapter_docs(records: List[dict]) -> List[Doc]:
    return [Doc(module=r["module"], record_id=r["record_id"], text=r["text"],
                subject=r.get("subject") or "", status=r["status"], origin=r["origin"],
                category=r["category"], sources=r["sources"], detail=dict(r["detail"]),
                terms=set(index_text(r["search_text"], r.get("subject") or "").split()),
                norm=" " + normalize(f"{r.get('subject') or ''} {r['search_text']}") + " ",
                owner=None, positive=polarity(r["text"]), subject_norm=normalize(r.get("subject") or ""))
            for r in records]


def _mentions(doc: Doc, e: Entity, entities: List[Entity]) -> bool:
    """The record talks about entity e (as a whole name, not as part of a
    longer known name such as 'Demo Garden Annex' for 'Demo Garden')."""
    if f" {e.key} " not in doc.norm:
        return False
    longer = [o for o in entities if o.key != e.key and e.key in o.key and f" {o.key} " in doc.norm]
    if not longer:
        return True
    rest = doc.norm
    for o in longer:
        rest = rest.replace(f" {o.key} ", " ")
    return f" {e.key} " in rest


_TO = (" to ", " to the ", " into ", " into the ", " a ", " al ", " a la ", " hacia ", " hacia el ",
       " hacia la ")


def _about(doc: Doc, e: Entity, entities: List[Entity], intents: List[str]) -> bool:
    """The record is ABOUT entity e - not merely mentioning it as a
    destination or comparison ("The Welcome Hall is larger than the Demo
    Garden"). With a subject set, e must be that subject; without one, e must
    be the first known name in the sentence. Exception for route questions:
    a record leading TO e ("... leads to the Crystal Courtyard") answers how
    to get there."""
    if "route" in intents and any(f"{t}{e.key} " in doc.norm for t in _TO):
        return True
    if doc.subject_norm:
        subj = " " + doc.subject_norm + " "
        if f" {e.key} " not in subj:
            return False
        return not any(o.key != e.key and e.key in o.key and f" {o.key} " in subj
                       for o in entities)
    first, pos = None, None
    for o in entities:
        i = doc.norm.find(f" {o.key} ")
        if i >= 0 and _mentions(doc, o, entities) and (pos is None or i < pos):
            first, pos = o, i
    return first is not None and first.key == e.key


def _intent_fit(doc: Doc, intent: str) -> bool:
    if intent == "identifier" and any(t.isdigit() for t in doc.terms):
        return True
    return bool(doc.terms & _INTENT_STEMS.get(intent, set()))


def search(store, sources, question: str, viewer: Optional[str] = None, limit: int = 10,
           modules: Optional[List[str]] = None, include_rejected: bool = False,
           use_fts: Optional[bool] = None) -> List[Hit]:
    viewer = viewer or store.local_user
    local = viewer == store.local_user
    want = (lambda m: modules is None or m in modules)

    # -- what the viewer may see -------------------------------------------------
    adapter_records: List[dict] = []
    names: List[Tuple[str, str]] = []
    if local:
        for src in sources:
            if want(src.module):
                adapter_records += src.records()
                names += [(n, src.module) for n in src.entity_names()]
    lib_on = want("library")
    entities = _entities(store, viewer, lib_on, tuple(sorted(set(names))))
    q: Query = analyze(question, entities)
    if not q.terms:
        return []
    q_positive = polarity(question)
    # known names + names written in the question that SK doesn't know yet
    known = entities + [e for e in q.entities if e not in entities]

    groups: Dict[str, Set[str]] = {t: {t} | set(synonyms(t)) for t in q.terms}
    every = sorted({w for g in groups.values() for w in g})

    lib_rows = store.search_rows(viewer, every, use_fts) if lib_on else []
    adapter_docs = _adapter_docs(adapter_records)
    docs = _library_docs(store, lib_rows) + adapter_docs
    docs = [d for d in docs if d.terms & set(every)]

    # word rarity from the viewer's visible knowledge only
    n_total = (store.visible_count(viewer) if lib_on else 0) + len(adapter_records)
    df = store.visible_term_counts(viewer, q.terms, use_fts) if lib_on else {t: 0 for t in q.terms}
    for r in adapter_docs:
        for t in q.terms:
            if t in r.terms:
                df[t] = df.get(t, 0) + 1
    n_total = max(n_total, 1)
    idf = {t: math.log(1 + (n_total - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)) for t in q.terms}
    total_w = sum(idf.values()) or 1.0

    scored: List[Tuple[float, Doc, dict]] = []
    for d in docs:
        matched, syn_used, got = [], [], 0.0
        for t in q.terms:
            if t in d.terms:
                got += idf[t]
                matched.append(t)
            else:
                hit = groups[t] & d.terms
                if hit:
                    got += SYNONYM_WEIGHT * idf[t]
                    syn_used.append(f"{t}~{sorted(hit)[0]}")
        relevance = got / total_w
        if relevance < MIN_RELEVANCE:
            continue
        factor, notes = 1.0, []
        entity_ok = True
        if q.entities:
            about = [e for e in q.entities if _about(d, e, known, q.intents)]
            mentioned = [e for e in q.entities if _mentions(d, e, known)]
            entity_ok = len(about) == len(q.entities)
            if not entity_ok:
                if mentioned:
                    factor *= 0.6
                    notes.append(f"mentions {', '.join(e.name for e in mentioned)} but is about "
                                 f"{d.subject or 'something else'}")
                elif d.subject_norm and d.subject_norm not in {e.key for e in q.entities}:
                    factor *= 0.45
                    notes.append(f"different subject: {d.subject}")
                else:
                    factor *= 0.6
                    notes.append("the subject you asked about is not mentioned")
        intent_ok = True
        if q.intents:
            # every requirement of the question must be met (conservative)
            fits = [i for i in q.intents if _intent_fit(d, i)]
            missing = [i for i in q.intents if i not in fits]
            intent_ok = not missing
            if not fits:
                factor *= 0.85
                notes.append(f"doesn't give the kind of information asked ({', '.join(q.intents)})")
            elif missing:
                notes.append(f"answers only part of the question: gives {', '.join(fits)}, "
                             f"not {', '.join(missing)}")
            else:
                factor *= 1.15
        polarity_ok = d.positive == q_positive
        if not polarity_ok:
            factor *= 0.5
            notes.append("opposite statement: the question is " +
                         ("affirmative" if q_positive else "negative") + ", this record is " +
                         ("affirmative" if d.positive else "negative"))
        if q.ambiguous:
            notes.append("ambiguous name: " + " / ".join(e.name for e in q.ambiguous))
        if d.status == "rejected" and not include_rejected:
            continue
        score = min(1.0, relevance * factor) + TRUST_BONUS.get(d.status, 0.0)
        if d.status in ("outdated", "rejected"):
            score -= 1.0                         # always after current knowledge
        answers = (entity_ok and intent_ok and polarity_ok and relevance >= ANSWER_RELEVANCE
                   and not q.ambiguous and d.status not in ("outdated", "rejected"))
        why = {"matched": matched, "synonyms": syn_used,
               "subjects": [e.name for e in q.entities], "intents": q.intents,
               "relevance": round(relevance, 3), "factor": round(factor, 3),
               "polarity": "affirmative" if d.positive else "negative",
               "matches_question": answers, "notes": notes}
        scored.append((score, d, why))

    # -- duplicates (visible ones only): one result, no extra weight ----------------
    best: Dict[str, Tuple[float, Doc, dict]] = {}
    dupes: Dict[str, List[Doc]] = {}
    for item in scored:
        key = item[1].module + "|" + item[1].norm
        dupes.setdefault(key, []).append(item[1])
        if key not in best or (item[1].status == "verified", item[0]) > \
                (best[key][1].status == "verified", best[key][0]):
            best[key] = item
    ranked = sorted(best.values(), key=lambda x: -x[0])

    scored_lib = {d.record_id: (sc, d, why) for sc, d, why in ranked if d.module == "library"}

    def make(item) -> Hit:
        score, d, why = item
        h = _hit(store, d, score, why, viewer)
        same = dupes[d.module + "|" + d.norm]
        if len(same) > 1:
            h.detail["duplicates"] = [x.record_id for x in same if x is not d]
            h.detail["stated_by"] = len({x.owner for x in same})
        return h

    # -- conflicts: every visible side shown, together, never cut by the limit ----
    # (results are only built for what is returned)
    out: List[Hit] = []
    placed: Set[str] = set()
    for item in ranked:
        d = item[1]
        if d.module + d.record_id in placed:
            continue
        h = make(item)
        group = [h]
        if h.module == "library" and h.status == "conflicting":
            for pid in _conflict_partners(store, int(h.record_id), viewer):
                k = str(pid)
                if "library" + k in placed or any(g.record_id == k for g in group):
                    continue
                partner = make(scored_lib[k]) if k in scored_lib else _partner_hit(store, pid, viewer)
                if partner is not None:
                    partner.detail["conflict_with"] = h.record_id
                    group.append(partner)
            if len(group) > 1:
                gid = min(int(g.record_id) for g in group)
                for g in group:
                    g.detail["conflict_group"] = gid
        for g in group:
            placed.add(g.module + g.record_id)
            out.append(g)
        if len(out) >= limit:
            break
    return out


_ENTITY_CACHE: Dict[tuple, List[Entity]] = {}


def _entities(store, viewer: str, lib_on: bool, adapter_names: tuple) -> List[Entity]:
    """Known names the viewer may see, cached until the database changes
    (key: viewer, claim count, last update, adapter names)."""
    stamp = store.change_stamp() if lib_on else None
    key = (store.path, viewer, lib_on, stamp, hash(adapter_names))
    hit = _ENTITY_CACHE.get(key)
    if hit is None:
        names = list(adapter_names)
        if lib_on:
            names += [(s, "library", n) for s, n in store.visible_subjects(viewer) if s]
        hit = make_entities(names)
        if len(_ENTITY_CACHE) > 32:
            _ENTITY_CACHE.clear()
        _ENTITY_CACHE[key] = hit
    return hit


def _hit(store, d: Doc, score: float, why: dict, viewer: str) -> Hit:
    if d.module == "library":
        s = store.evidence_summary(int(d.record_id))
        sources = [f"{s['direct']} direct, {s['confirmations']} confirmations, "
                   f"{s['assertions']} unsupported statements"]
    else:
        sources = d.sources
    detail = dict(d.detail or {})
    detail["why"] = why
    return Hit(d.module, d.record_id, d.text, d.status, d.origin, round(score, 4), d.category,
               sources, detail)


def _conflict_partners(store, claim_id: int, viewer: str) -> List[int]:
    out = []
    for link in store.links(claim_id, viewer):
        if link["kind"] != "contradicts":
            continue
        other = link["to_claim"] if link["from_claim"] == claim_id else link["from_claim"]
        if store.claim(other, viewer) is not None:          # only sides the viewer may see
            out.append(other)
    return out


def _partner_hit(store, claim_id: int, viewer: str) -> Optional[Hit]:
    c = store.claim(claim_id, viewer)
    if c is None or c["status"] == "rejected":
        return None
    d = Doc(module="library", record_id=str(c["id"]), text=c["text"], subject=c["subject"] or "",
            status=c["status"], origin=c["origin"], category=c["category"] or "",
            detail={"version": c["version"], "scope": c["scope"]}, terms=set(terms(c["text"])),
            norm=" " + normalize(c["text"]) + " ", owner=c["owner_id"],
            positive=polarity(c["text"]), subject_norm=normalize(c["subject"] or ""))
    return _hit(store, d, 0.0, {"matched": [], "synonyms": [], "subjects": [], "intents": [],
                                "relevance": 0.0, "factor": 0.0, "matches_question": False,
                                "notes": ["shown because it contradicts a result"]}, viewer)
