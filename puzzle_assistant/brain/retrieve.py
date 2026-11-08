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

import gc
import heapq
import math
import re
import weakref
from typing import Dict, List, Optional, Set, Tuple

from .facts import extract as extract_fact
from .lexicon import SYNONYM_WEIGHT, synonyms
from .model import Hit
from .query import INTENT_EVIDENCE, Entity, Query, analyze, make_entities
from .text import absence_terms, index_text, normalize, polarity, stem, terms

MIN_RELEVANCE = 0.3          # below this a record is not shown at all
ANSWER_RELEVANCE = 0.5       # needed (with subject + intent fit) to count as matching the question
TRUST_BONUS = {"verified": 0.06, "awaiting_verification": 0.02}
# Testing switch: when True every candidate also gets the full original
# scoring and any difference from the fast path raises (tests turn it on).
VERIFY_FAST = False
# Reference mode for tests: every candidate gets full() and the ranking is a
# plain sort - the way search worked before the latency work. The fast path
# must return exactly the same results.
EXHAUSTIVE = False
MAX_FACTOR = 1.15            # every requirement of the question met; all other factors <= 1
_INTENT_STEMS = {k: {stem(w) for w in v} for k, v in INTENT_EVIDENCE.items()}


# A different direction is a different answer ("south door" is not the
# "north door"): if the question names one, a matching record must too.
_DIRECTIONS = [frozenset({stem(w)} | set(synonyms(stem(w)))) for w in ("north", "south", "east", "west")]
# Yes/no questions: a negative record ("the taxi does not work there") answers
# an affirmative yes/no question just as well as a positive one.
_YES_NO = re.compile(r"^(does|do|did|is|are|was|were|can|could|will|has|have|should|may|"
                     r"puedo|puede|se puede|funciona|esta|es|hay|tiene)\b")
# ...and a Spanish question (opening "¿") without a question word ("¿Se
# permiten mascotas...?"): those rarely start with an auxiliary verb.
_WH = re.compile(r"\b(what|which|who|whom|whose|where|when|why|how|que|cual|cuales|quien|"
                 r"quienes|donde|adonde|cuando|como|cuanto|cuanta|cuantos|cuantas)\b")


def _is_yes_no(question: str) -> bool:
    n = normalize(question)
    if _YES_NO.match(n):
        return True
    return question.strip().startswith("¿") and not _WH.search(n)


_UNSET = object()


class Doc:
    __slots__ = ("module", "record_id", "text", "subject", "status", "origin", "category",
                 "sources", "detail", "terms", "norm", "owner", "positive", "subject_norm",
                 "has_digit", "absence", "_fact")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        self.has_digit = any(t.isdigit() for t in self.terms or ())
        # what a negative record says is absent ("has no caretaker"), if anything
        self.absence = absence_terms(self.text or "") if self.positive is False else frozenset()
        self._fact = _UNSET

    @property
    def fact(self):
        """The structured fact this record states (B3, brain/facts.py), or None.
        Read once; cached with the record, so it follows the same invalidation."""
        if self._fact is _UNSET:
            self._fact = extract_fact(self.text or "", self.subject or "") \
                if self.module == "library" else None
        return self._fact


def _library_doc(r: dict) -> Doc:
    return Doc(module="library", record_id=str(r["id"]), text=r["text"],
               subject=r["subject"] or "", status=r["status"], origin=r["origin"],
               category=r["category"] or "", sources=None,
               detail={"version": r["version"], "scope": r["scope"]},
               terms=frozenset(r["terms"].split()), norm=" " + r["norm"] + " ",
               owner=r["owner_id"], positive=bool(r["positive"]),
               subject_norm=r["subject_norm"])


class _StoreCache:
    """What one KnowledgeStore keeps between searches (gone with the store).

      docs      prepared search data per claim id. WHICH claims a search may
                use is still asked from the database on every search (the
                visibility rule, store.visible_keys); this only saves
                re-reading and re-parsing their text.
      names     known-name lists per viewer (see _entities).

    Kept exact by two rules:
      * changes through this store: every change to a claim records an event
        on it in the same transaction, so entries of claims with events after
        `mark` are dropped (store.changed_since);
      * changes by ANY other connection - another program, a second Brain, a
        raw SQL edit, a restored backup: SQLite's data_version moves, and
        everything is dropped and rebuilt (nothing is assumed about how the
        other connection wrote). Also if the event log ever goes backwards.
    All of it happens under the store's lock, which every write also takes."""

    def __init__(self):
        self.reset()
        self.data_version = None

    def reset(self) -> None:
        self.docs: Dict[int, "Doc"] = {}
        self.mark = -1
        self.names: Dict[tuple, tuple] = {}


_CACHES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _cache(store) -> _StoreCache:
    """The store's cache, emptied first if another connection committed."""
    c = _CACHES.get(store)
    if c is None:
        c = _CACHES[store] = _StoreCache()
    dv = store.data_version()
    if c.data_version is not None and dv != c.data_version:
        c.reset()
    c.data_version = dv
    return c


def _visible_docs(store, cache: _StoreCache, viewer: str, every: List[str], use_fts):
    """(docs the viewer may see that contain one of `every`, all visible docs or
    None). In plain-search mode the word counts come from the same visible
    claims. Call with store._lock held."""
    mark, changed = store.changed_since(max(cache.mark, 0))
    if mark < cache.mark:                      # event log went backwards: a different file
        cache.reset()
    elif cache.mark >= 0:
        for cid in changed:
            cache.docs.pop(cid, None)
    cache.mark = mark
    keys, filtered = store.visible_keys(viewer, every, use_fts, ids_only=True)
    docs_of = cache.docs
    miss = [k[0] for k in keys if k[0] not in docs_of]
    if miss:
        for r in store.index_rows(viewer, miss):
            docs_of[r["id"]] = _library_doc(r)
    docs = [docs_of[k[0]] for k in keys if k[0] in docs_of]
    if filtered:
        return docs, None
    want = set(every)
    return [d for d in docs if d.terms & want], docs


def visible_library_docs(store, viewer: Optional[str]) -> List[Doc]:
    """Every library record the viewer's search may use (B3 reasoning reads its
    facts from these - the same visibility rule and cache as search)."""
    viewer = viewer or store.local_user
    with store._lock:
        docs, _ = _visible_docs(store, _cache(store), viewer, [], None)
    return docs


def _adapter_docs(records: List[dict]) -> List[Doc]:
    return [Doc(module=r["module"], record_id=r["record_id"], text=r["text"],
                subject=r.get("subject") or "", status=r["status"], origin=r["origin"],
                category=r["category"], sources=r["sources"], detail=dict(r["detail"]),
                terms=frozenset(index_text(r["search_text"], r.get("subject") or "").split()),
                norm=" " + normalize(f"{r.get('subject') or ''} {r['search_text']}") + " ",
                owner=None, positive=polarity(r["text"]), subject_norm=normalize(r.get("subject") or ""))
            for r in records]


class Known(list):
    """The known names of one search (a list, in its usual order) with two
    lookups worked out once instead of for every record: the longer names
    containing a name, and the names starting with a given word."""

    def __init__(self, items=()):
        super().__init__(items)
        self._longer: Dict[str, List[Entity]] = {}
        self._by_word: Optional[Dict[str, List[int]]] = None

    def longer(self, e: Entity) -> List[Entity]:
        """Other names containing e.key, in list order."""
        r = self._longer.get(e.key)
        if r is None:
            r = self._longer[e.key] = [o for o in self if o.key != e.key and e.key in o.key]
        return r

    def starting_with(self, words: Set[str]) -> List[int]:
        """Positions (ascending) of names whose first word is in `words`."""
        if self._by_word is None:
            self._by_word = {}
            for n, o in enumerate(self):
                self._by_word.setdefault(o.key.split(" ", 1)[0], []).append(n)
        return sorted(n for w in words for n in self._by_word.get(w, ()))


def _longer(entities, e: Entity) -> List[Entity]:
    if isinstance(entities, Known):
        return entities.longer(e)
    return [o for o in entities if o.key != e.key and e.key in o.key]


def _mentions(doc: Doc, e: Entity, entities: List[Entity]) -> bool:
    """The record talks about entity e (as a whole name, not as part of a
    longer known name such as 'Demo Garden Annex' for 'Demo Garden')."""
    if f" {e.key} " not in doc.norm:
        return False
    longer = [o for o in _longer(entities, e) if f" {o.key} " in doc.norm]
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
        return not any(f" {o.key} " in subj for o in _longer(entities, e))
    first, pos = None, None
    # a name can only occur if its first word is a word of the record
    order = entities.starting_with(set(doc.norm.split())) if isinstance(entities, Known) \
        else range(len(entities))
    for n in order:
        o = entities[n]
        i = doc.norm.find(f" {o.key} ")
        if i >= 0 and _mentions(doc, o, entities) and (pos is None or i < pos):
            first, pos = o, i
    return first is not None and first.key == e.key


def _intent_fit(doc: Doc, intent: str) -> bool:
    if intent == "identifier" and doc.has_digit:
        return True
    return bool(doc.terms & _INTENT_STEMS.get(intent, set()))


def search(store, sources, question: str, viewer: Optional[str] = None, limit: int = 10,
           modules: Optional[List[str]] = None, include_rejected: bool = False,
           use_fts: Optional[bool] = None) -> List[Hit]:
    """One search. Python's cyclic garbage collector is paused while it runs
    (a search creates many short-lived objects; a full collection scanning the
    cached index mid-search cost up to ~200 ms). Objects are still freed as
    usual; only the cycle scan waits until the search is done."""
    was_on = gc.isenabled()
    gc.disable()
    try:
        return _search(store, sources, question, viewer, limit, modules, include_rejected,
                       use_fts)
    finally:
        if was_on:
            gc.enable()


def _search(store, sources, question: str, viewer: Optional[str] = None, limit: int = 10,
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
    with store._lock:                          # cache upkeep and the reads it relies on
        entities = _entities(store, _cache(store), viewer, lib_on, tuple(sorted(set(names))))
    q: Query = analyze(question, entities)
    if not q.terms:
        return []
    q_positive = polarity(question)
    # known names + names written in the question that SK doesn't know yet
    known = Known(entities + [e for e in q.entities if e not in entities])

    groups: Dict[str, Set[str]] = {t: {t} | set(synonyms(t)) for t in q.terms}
    # what the question asks beyond the names in it (a record matching only the
    # name - "closes at midnight" for "what connects to X" - is not an answer)
    name_terms: Set[str] = set()
    for e in q.entities:
        name_terms |= set(terms(e.key))
    asked = [t for t in q.terms if t not in name_terms]
    q_dirs = [g for g in _DIRECTIONS if g & set(q.terms)]
    yes_no = q_positive and _is_yes_no(question)
    # a record saying the asked-about thing is ABSENT answers the question
    asked_words = frozenset(w for t in asked for w in groups[t])
    every = sorted({w for g in groups.values() for w in g})

    if lib_on:
        with store._lock:
            lib_docs, all_visible = _visible_docs(store, _cache(store), viewer, every, use_fts)
    else:
        lib_docs, all_visible = [], None
    adapter_docs = _adapter_docs(adapter_records)
    every_set = set(every)
    docs = [d for d in lib_docs + adapter_docs if d.terms & every_set]

    # word rarity from the viewer's visible knowledge only
    n_total = (store.visible_count(viewer) if lib_on else 0) + len(adapter_records)
    if not lib_on:
        df = {t: 0 for t in q.terms}
    elif all_visible is not None:            # plain search: count in the visible claims we hold
        df = {t: sum(1 for d in all_visible if t in d.terms) for t in q.terms}
    else:
        df = store.visible_term_counts(viewer, q.terms, use_fts)
    for r in adapter_docs:
        for t in q.terms:
            if t in r.terms:
                df[t] = df.get(t, 0) + 1
    n_total = max(n_total, 1)
    idf = {t: math.log(1 + (n_total - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)) for t in q.terms}
    total_w = sum(idf.values()) or 1.0

    # Exact scores, computed cheaply. A record's score depends only on which
    # question words / synonyms / kind-of-question words it contains, whether
    # it has a number, its polarity and status, and - for the names in the
    # question - where they occur. Records containing none of those names
    # (most of them) share their score with every record of the same
    # "signature", so it is computed once per signature (fast_score: the
    # same arithmetic, in the same order, as full()). Records containing a
    # name get full(). The explanation ("why") is only built for records
    # that are returned. VERIFY_FAST checks fast == full for every record.
    def full(d: Doc, relevance: float):
        """The complete original scoring, with its explanation."""
        matched, syn_used = [], []
        for t in q.terms:
            if t in d.terms:
                matched.append(t)
            else:
                hit = groups[t] & d.terms
                if hit:
                    syn_used.append(f"{t}~{sorted(hit)[0]}")
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
        if q.ambiguous and not q.entities and \
                not any(_mentions(d, e, known) for e in q.ambiguous):
            factor *= 0.45                       # about none of the places the name could mean
            notes.append(f"different subject: {d.subject or 'something else'}")
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
                factor *= MAX_FACTOR             # the only factor above 1
        content_ok = not asked or any(t in matched or groups[t] & d.terms for t in asked)
        if not content_ok:
            notes.append("only the name matches; not what was asked about it")
        direction_ok = all(g & d.terms for g in q_dirs)
        if not direction_ok:
            factor *= 0.6
            notes.append("a different direction than the one asked about")
        # only about the place the question names: "Who is the caretaker of the
        # Sala Azul?" <- "The Sala Azul has no caretaker" (not for "who is the caretaker?")
        absent = bool(q.entities) and entity_ok and q_positive and not d.positive and \
            not d.absence.isdisjoint(asked_words)
        polarity_ok = d.positive == q_positive or (yes_no and not d.positive) or absent
        if absent:
            notes.append("documented absence: says there is no " +
                         ", ".join(sorted(d.absence & asked_words)))
        elif yes_no and not d.positive:
            notes.append("a 'no' answer to a yes/no question")
        elif not polarity_ok:
            factor *= 0.5
            notes.append("opposite statement: the question is " +
                         ("affirmative" if q_positive else "negative") + ", this record is " +
                         ("affirmative" if d.positive else "negative"))
        if q.ambiguous:
            notes.append("ambiguous name: " + " / ".join(e.name for e in q.ambiguous))
        score = min(1.0, relevance * factor) + TRUST_BONUS.get(d.status, 0.0)
        if d.status in ("outdated", "rejected"):
            score -= 1.0                         # always after current knowledge
        answers = (entity_ok and intent_ok and polarity_ok and content_ok and direction_ok
                   and relevance >= ANSWER_RELEVANCE
                   and not q.ambiguous and d.status not in ("outdated", "rejected"))
        why = {"matched": matched, "synonyms": syn_used,
               "subjects": [e.name for e in q.entities], "intents": q.intents,
               "relevance": round(relevance, 3), "factor": round(factor, 3),
               "polarity": "affirmative" if d.positive else "negative",
               "matches_question": answers, "notes": notes}
        return (score, d, why)

    rel_memo: Dict[frozenset, float] = {}

    def relevance_of(proj: frozenset) -> float:
        r = rel_memo.get(proj)
        if r is None:
            got = 0.0
            for t in q.terms:
                if t in proj:
                    got += idf[t]
                elif not groups[t].isdisjoint(proj):
                    got += SYNONYM_WEIGHT * idf[t]
            r = rel_memo[proj] = got / total_w
        return r

    entity_keys = {e.key for e in q.entities}
    name_marks = [f" {e.key} " for e in q.entities]
    amb_marks = [f" {e.key} " for e in q.ambiguous] if q.ambiguous and not q.entities else []
    sig_words = frozenset(every_set.union(*(_INTENT_STEMS.get(i, set()) for i in q.intents),
                                          *q_dirs))

    def fast_score(relevance, proj, has_digit, positive, status, other_subject) -> float:
        factor = 1.0
        if q.entities:                           # none of the names occurs in the record
            factor *= 0.45 if other_subject else 0.6
        if amb_marks:
            factor *= 0.45
        if q.intents:
            fits = [i for i in q.intents if (i == "identifier" and has_digit) or
                    not proj.isdisjoint(_INTENT_STEMS.get(i, ()))]
            if not fits:
                factor *= 0.85
            elif len(fits) == len(q.intents):
                factor *= MAX_FACTOR
        if not all(not g.isdisjoint(proj) for g in q_dirs):
            factor *= 0.6
        if not (yes_no and not positive) and positive != q_positive:
            factor *= 0.5                        # (absence only applies to named records)
        score = min(1.0, relevance * factor) + TRUST_BONUS.get(status, 0.0)
        if status in ("outdated", "rejected"):
            score -= 1.0
        return score

    cands = []                                   # (doc, relevance, score) in record order
    full_items: Dict[int, Tuple[float, Doc, dict]] = {}
    sig_memo: Dict[tuple, float] = {}
    marks = name_marks + amb_marks
    skip_rejected = not include_rejected
    for d in docs:
        proj = sig_words & d.terms           # frozenset whatever d.terms is
        relevance = rel_memo.get(proj)
        if relevance is None:
            relevance = relevance_of(proj)
        if relevance < MIN_RELEVANCE:
            continue
        if skip_rejected and d.status == "rejected":
            continue
        named = EXHAUSTIVE
        for mk in marks:
            if mk in d.norm:
                named = True
                break
        if named:
            item = full_items[id(d)] = full(d, relevance)
            score = item[0]
        else:
            other = bool(d.subject_norm and d.subject_norm not in entity_keys)
            sig = (proj, d.has_digit, d.positive, d.status, other)
            score = sig_memo.get(sig)
            if score is None:
                score = sig_memo[sig] = fast_score(relevance, proj, d.has_digit, d.positive,
                                                   d.status, other)
            if VERIFY_FAST:
                exact = full(d, relevance)[0]
                if exact != score:
                    raise AssertionError(f"fast score {score!r} != full {exact!r} for {d.text!r}")
        cands.append((d, relevance, score))

    def full_item(c) -> Tuple[float, Doc, dict]:
        item = full_items.get(id(c[0]))
        if item is None:
            item = full_items[id(c[0])] = full(c[0], c[1])
        return item

    # duplicates (same wording, visible ones only): one result, no extra weight
    dupes: Dict[str, List[Doc]] = {}
    members: Dict[str, list] = {}
    for c in cands:
        key = c[0].module + "|" + c[0].norm
        dupes.setdefault(key, []).append(c[0])
        members.setdefault(key, []).append(c)
    if EXHAUSTIVE:                               # reference: plain stable sort
        best_ref: Dict[str, tuple] = {}
        for c in cands:
            key = c[0].module + "|" + c[0].norm
            if key not in best_ref or (c[0].status == "verified", c[2]) > \
                    (best_ref[key][0].status == "verified", best_ref[key][2]):
                best_ref[key] = c
        ranked_ref = sorted(best_ref.values(), key=lambda c: -c[2])
    best_of: Dict[str, tuple] = {}
    heap = []
    for n, (key, ms) in enumerate(members.items()):     # first-seen order breaks ties
        best = None
        for c in ms:                                     # verified first, then score
            if best is None or (c[0].status == "verified", c[2]) > \
                    (best[0].status == "verified", best[2]):
                best = c
        best_of[key] = best
        heap.append((-best[2], n, key))
    heapq.heapify(heap)
    group_of = {c[0].record_id: c[0].module + "|" + c[0].norm
                for c in cands if c[0].module == "library"}

    def ranked_items():
        if EXHAUSTIVE:
            for c in ranked_ref:
                yield full_item(c)
            return
        while heap:
            yield full_item(best_of[heapq.heappop(heap)[2]])

    def scored_lib(record_id: str):
        """The scored representative for this record, if it is one (else None)."""
        key = group_of.get(record_id)
        if key is None or best_of[key][0].record_id != record_id:
            return None
        return full_item(best_of[key])

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
    for item in ranked_items():
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
                lib_item = scored_lib(k)
                partner = make(lib_item) if lib_item is not None else _partner_hit(store, pid, viewer)
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


def _entities(store, cache: _StoreCache, viewer: str, lib_on: bool,
              adapter_names: tuple) -> List[Entity]:
    """Known names the viewer may see. Kept until a new claim, a correction or
    a sharing step (store.subjects_stamp); after new claims only their
    subjects are read and added - the same list, in the same order, as
    reading everything again (both sorted). Call with store._lock held."""
    key = (viewer, lib_on, adapter_names)
    sub = store.subjects_stamp() if lib_on else None
    hit = cache.names.get(key)
    if hit is not None and hit[0] == sub:
        return hit[2]
    names = list(adapter_names)
    if lib_on:
        if hit is not None and hit[0][1:] == sub[1:] and hit[0][0] < sub[0]:
            # sorted like the full query (Python and SQLite's default collation
            # order strings the same way)
            pairs = sorted(set(hit[1]).union(store.visible_subjects(viewer,
                                                                    after_id=hit[0][0])))
        else:
            pairs = store.visible_subjects(viewer)
        names += [(s, "library", n) for s, n in pairs if s]
    else:
        pairs = []
    ents = make_entities(names)
    if len(cache.names) >= 32:                 # many viewers: keep it bounded
        cache.names.clear()
    cache.names[key] = (sub, pairs, ents)
    return ents


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
        if store.searchable(store.claim(other, viewer), viewer):   # shared or the viewer's own
            out.append(other)
    return out


def _partner_hit(store, claim_id: int, viewer: str) -> Optional[Hit]:
    c = store.claim(claim_id, viewer)
    if not store.searchable(c, viewer) or c["status"] == "rejected":
        return None
    d = Doc(module="library", record_id=str(c["id"]), text=c["text"], subject=c["subject"] or "",
            status=c["status"], origin=c["origin"], category=c["category"] or "",
            detail={"version": c["version"], "scope": c["scope"]}, terms=set(terms(c["text"])),
            norm=" " + normalize(c["text"]) + " ", owner=c["owner_id"],
            positive=polarity(c["text"]), subject_norm=normalize(c["subject"] or ""))
    return _hit(store, d, 0.0, {"matched": [], "synonyms": [], "subjects": [], "intents": [],
                                "relevance": 0.0, "factor": 0.0, "matches_question": False,
                                "notes": ["shown because it contradicts a result"]}, viewer)
