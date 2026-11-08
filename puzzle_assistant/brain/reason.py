"""Connected facts (B3): answers that need one or more facts chained.

  "Who maintains the room the northern exit leads to?"
     step 1  leads_to(<place of the exit>, north) -> Crystal Courtyard
     step 2  caretaker(Crystal Courtyard)         -> Mira

How a question becomes steps: a small grammar (English / Spanish) splits it
into an outer ask ("who maintains X", "room ID of X", "coins in X") and a
room description X ("the room the north exit of P leads to", "the room
Rafael looks after", "la sala donde Don Tito es el encargado") - nested up
to MAX_STEPS. A question outside this grammar is not reasoned about.

How a step is resolved: only facts (brain/facts.py) of claims the asker's
search may see (the B2 visibility rule; never anyone else's private claim);
rejected and outdated claims are ignored. A step links to the next ONLY
through the exact same normalised place name - never through a shared
source, a similar name or a partial name ("the Faro" is ambiguous, never
resolved).

  nothing documented          -> cannot establish (missing). Missing is
                                 never read as "there is none".
  "has no caretaker" claim    -> documented absence
  two different values, or a
  claim marked conflicting    -> cannot establish (conflicting / ambiguous)
  one value                   -> that value

The answer is ESTABLISHED only if every claim used is verified (evidence +
review, B1 rules). Otherwise SK says it cannot establish it and shows the
unverified possibility, labelled. Its audience is "shared" only if every
claim used is shared - a private claim never becomes part of a shared answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .facts import DIR, EXIT, Fact, display, key, _DIRS
from .text import tokens

MAX_STEPS = 3

_ROLE = {"caretaker": "caretaker", "runs": "runner"}
_LABEL = {"leads_to": "where it leads", "caretaker": "who looks after it", "runs": "who runs it",
          "room_id": "its room ID", "quest_reward": "its quest reward"}


# -- question plans --------------------------------------------------------------------
@dataclass
class Ask:
    """Find `relation` of a place; the place is a name or another Ask
    (inverse=True: find the PLACE whose relation value is `value`)."""
    relation: str
    place: object = None              # str (name key) | Ask
    qualifier: str = ""
    inverse: bool = False
    value: str = ""                   # inverse: the person


ROOM = r"(?:room|place|sala|habitacion|lugar)"
A = r"(?:the |el |la |los |las )?"
DE = r"(?:del|de la|de los|de las|de|of the|of)"

# room descriptions -> Ask (or a name)
_DESC: List[Tuple["re.Pattern", str]] = [
    (re.compile(rf"^{A}{ROOM} (?:that |which )?{A}(?:(?P<q>{DIR}) )?{EXIT} "
                r"(?:leads|goes|connects) (?:to|into)$"), "leads_to_anywhere"),
    (re.compile(rf"^{A}{ROOM} (?:a la que|adonde|a donde|al que) (?:lleva|conduce|va) "
                rf"{A}{EXIT} (?:(?P<q>{DIR}))?$"), "leads_to_anywhere"),
    (re.compile(rf"^{A}{ROOM} (?:that |which )?{A}(?:(?P<q>{DIR}) )?{EXIT} {DE} (?P<inner>.+?) "
                r"(?:leads|goes|connects) (?:to|into)$"), "leads_to"),
    (re.compile(rf"^{A}{ROOM} (?:that |which )?(?P<inner>.+?) {A}(?:(?P<q>{DIR}) )?{EXIT} "
                r"(?:leads|goes|connects) (?:to|into)$"), "leads_to_from"),
    (re.compile(rf"^{A}{ROOM} (?:a la que|adonde|a donde|al que) (?:lleva|conduce|va) "
                rf"{A}{EXIT} (?:(?P<q>{DIR}) )?{DE} (?P<inner>.+)$"), "leads_to"),
    (re.compile(rf"^{A}{ROOM} (?:that |which )?(?P<who>.+?) (?:maintains|maintain|takes care of|"
                r"cares for)$"), "caretaker_of"),
    (re.compile(rf"^{A}{ROOM} (?:where|in which) (?P<who>.+?) (?:is|works as) (?:the |a )?"
                r"(?:caretaker|keeper|maintainer|encargado|encargada)$"), "caretaker_of"),
    (re.compile(rf"^{A}{ROOM} (?:that |which )?(?P<who>.+?) is the (?:caretaker|keeper|maintainer) "
                r"of$"), "caretaker_of"),
    (re.compile(rf"^{A}{ROOM} (?:that |which )?(?P<who>.+?) runs$"), "runs_of"),
    (re.compile(rf"^{A}{ROOM} (?:que|la que) (?:cuida|mantiene) (?P<who>.+)$"), "caretaker_of"),
    (re.compile(rf"^{A}{ROOM} (?:donde|en la que|en que) (?P<who>.+?) es {A}"
                r"(?:encargad[oa]|cuidador[a]?)$"), "caretaker_of"),
    (re.compile(rf"^{A}{ROOM} (?:de la que|del que) (?P<who>.+?) es {A}"
                r"(?:encargad[oa]|cuidador[a]?)$"), "caretaker_of"),
]
# outer questions -> relation of (description)
_OUTER: List[Tuple["re.Pattern", str]] = [
    (re.compile(r"^who (?:is |s )?(?:the )?(?:caretaker|keeper|maintainer|custodian) of (?P<r>.+)$"),
     "caretaker"),
    (re.compile(r"^who (?:maintains|maintain|takes care of|cares for) (?P<r>.+)$"), "caretaker"),
    (re.compile(r"^who runs (?P<r>.+)$"), "runs"),
    (re.compile(r"^(?:what is |what s )?(?:the )?(?:room )?(?:id|number|code) of (?P<r>.+)$"),
     "room_id"),
    (re.compile(r"^how many coins does (?:the )?quest (?:in|of|at) (?P<r>.+?) give$"),
     "quest_reward"),
    (re.compile(r"^how many coins does (?:the )?quest give (?:in|at) (?P<r>.+)$"), "quest_reward"),
    (re.compile(rf"^where (?:does|do) {A}(?:(?P<q>{DIR}) )?{EXIT} (?:of|from) (?P<r>.+?) "
                r"(?:lead|go)(?: to)?$"), "leads_to"),
    (re.compile(r"^quien es (?:el |la )?(?:encargad[oa]|cuidador[a]?) (?:del|de la|de) (?P<r>.+)$"),
     "caretaker"),
    (re.compile(r"^quien (?:cuida|mantiene) (?:el |la |al |a la )?(?P<r>.+)$"), "caretaker"),
    (re.compile(r"^cual es (?:el )?(?:id|numero|codigo)(?: de sala)? (?:del|de la|de) (?P<r>.+)$"),
     "room_id"),
    (re.compile(r"^cuantas monedas da (?:la )?mision (?:del|de la|de|en) (?P<r>.+)$"),
     "quest_reward"),
    (re.compile(rf"^a donde (?:lleva|conduce|va) {A}{EXIT} (?:(?P<q>{DIR}) )?(?:del|de la|de) "
                r"(?P<r>.+)$"), "leads_to"),
]
# "the room that / where / que ..." - a description, even if we can't read it
# (a NAME may start with a room word: "the Sala Azul" is a name)
_UNPARSED_DESC = re.compile(rf"^{A}{ROOM} (?:that|which|where|in which|que|donde|en la que|"
                            r"a la que|adonde|a donde|de la que|del que|al que)\b")
# a room description that is itself introduced by a preposition ("in the room X")
_LEAD_IN = re.compile(r"^(?:in |at |en )")


ANYWHERE = "*"          # "the northern exit" - of whichever place documents one


def _qual(m) -> str:
    return _DIRS.get((m.groupdict().get("q") or "").strip(), "")


def _describe(text: str, depth: int):
    """A room description -> name key | Ask | None (not understood)."""
    text = _LEAD_IN.sub("", text.strip())
    for rx, kind in _DESC:
        m = rx.match(text)
        if not m:
            continue
        if depth >= MAX_STEPS:
            return None
        if kind == "leads_to_anywhere":
            return Ask("leads_to", ANYWHERE, _qual(m))
        if kind in ("leads_to", "leads_to_from"):
            inner = _describe(m.group("inner"), depth + 1)
            return None if inner is None else Ask("leads_to", inner, _qual(m))
        who = key(m.group("who"))
        if not who or len(who.split()) > 4:
            return None
        return Ask("caretaker" if kind == "caretaker_of" else "runs", inverse=True, value=who)
    if _UNPARSED_DESC.match(text):              # "the room that ..." we don't understand
        return None
    k = key(text)
    return k if k and len(k.split()) <= 6 else None


def plan(question: str) -> Optional[Ask]:
    """The steps for a question, or None if it is outside the grammar."""
    n = " ".join(tokens(question))
    for rx, relation in _OUTER:
        m = rx.match(n)
        if m:
            place = _describe(m.group("r"), 1)
            if place is None:
                return None
            return Ask(relation, place, _qual(m))
    return None


def steps_of(a) -> int:
    if isinstance(a, Ask):
        return 1 + (steps_of(a.place) if isinstance(a.place, Ask) else 0)
    return 0


# -- resolution ------------------------------------------------------------------------
@dataclass
class Support:
    claim_id: int
    text: str
    status: str
    scope: str
    fact: Fact


@dataclass
class StepResult:
    ask: str                               # what this step looked for
    outcome: str                           # value | absent | missing | conflicting | ambiguous
    value: Optional[str] = None            # key of the value (place / person / id)
    display: str = ""
    verified: bool = False
    supports: List[Support] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)   # conflicting / ambiguous values
    # verified step: unverified claims stating the same - shown, never evidence
    also_unverified: List[Support] = field(default_factory=list)


def _verified_only(ss: List[Support]) -> Tuple[bool, List[Support], List[Support]]:
    """(verified?, evidence, also stated unverified). A verified step rests on
    its verified claims only; an unverified step shows all its claims."""
    good = [s for s in ss if s.status == "verified"]
    if good:
        return True, good, [s for s in ss if s.status != "verified"]
    return False, ss, []


class FactIndex:
    """Facts of the claims one viewer's search may see."""

    def __init__(self, docs):
        self.by_place: Dict[Tuple[str, str], List[Support]] = {}
        self.by_value: Dict[Tuple[str, str], List[Support]] = {}
        self.places = set()
        self._shown: Dict[str, str] = {}
        for d in docs:
            f = d.fact
            if f is None or d.status in ("rejected", "outdated"):
                continue
            s = Support(int(d.record_id), d.text, d.status, (d.detail or {}).get("scope", ""), f)
            self.by_place.setdefault((f.relation, f.place), []).append(s)
            if f.value is not None:
                self.by_value.setdefault((f.relation, f.value), []).append(s)
            self.places.add(f.place)
            shown = display(d.text, f.place)
            if shown == f.place and d.subject:          # place taken from the claim's subject
                shown = display(d.subject, f.place)
            self._shown.setdefault(f.place, shown)
            if f.relation == "leads_to" and f.value:
                self.places.add(f.value)
                self._shown.setdefault(f.value, f.display)

    def shown(self, place: str) -> str:
        """A place name as written in a claim (capitals, accents)."""
        return self._shown.get(place, place)


def _settle(label: str, sups: List[Support]) -> StepResult:
    if not sups:
        return StepResult(label, "missing")
    values: Dict[Optional[str], List[Support]] = {}
    for s in sups:
        values.setdefault(s.fact.value, []).append(s)
    if len(values) > 1 or any(s.status == "conflicting" for s in sups):
        shown = [("none (documented absence)" if v is None else ss[0].fact.display or v)
                 for v, ss in values.items()]
        return StepResult(label, "conflicting", supports=sups, candidates=shown)
    (value, ss), = values.items()
    verified, evidence, also = _verified_only(ss)
    if value is None:
        return StepResult(label, "absent", None, "", verified, evidence, also_unverified=also)
    return StepResult(label, "value", value, evidence[0].fact.display or value, verified, evidence,
                      also_unverified=also)


def _ambiguous_name(index: FactIndex, name: str) -> List[str]:
    if name in index.places:
        return []
    padded = f" {name} "
    return sorted(p for p in index.places if padded in f" {p} " and p != name)


def resolve(a, index: FactIndex, out: List[StepResult]) -> Optional[str]:
    """Resolve `a`, appending each step's result to `out`; returns the place /
    value key it found, or None if the chain stops (the reason is in out[-1])."""
    if isinstance(a, str):
        parts = _ambiguous_name(index, a)
        if len(parts) >= 2:
            out.append(StepResult(f"the place called '{a}'", "ambiguous",
                                  candidates=[index.shown(p) for p in parts]))
            return None
        return a
    if a.inverse:
        label = f"the room whose {_ROLE[a.relation]} is {a.value}"
        sups = index.by_value.get((a.relation, a.value), [])
        places: Dict[str, List[Support]] = {}
        for s in sups:
            places.setdefault(s.fact.place, []).append(s)
        if not places:
            out.append(StepResult(label, "missing"))
            return None
        if any(s.status == "conflicting" for s in sups):
            out.append(StepResult(label, "conflicting", supports=sups,
                                  candidates=sorted(index.shown(p) for p in places)))
            return None
        if len(places) > 1:
            out.append(StepResult(label, "ambiguous", supports=sups,
                                  candidates=sorted(index.shown(p) for p in places)))
            return None
        (place, ss), = places.items()
        verified, evidence, also = _verified_only(ss)
        out.append(StepResult(label, "value", place, display(evidence[0].text, place), verified,
                              evidence, also_unverified=also))
        return place
    if a.place == ANYWHERE:
        side = f"the {a.qualifier} exit" if a.qualifier else "the exit"
        sups = [s for (rel, _), ss in index.by_place.items() if rel == a.relation for s in ss
                if not a.qualifier or s.fact.qualifier == a.qualifier]
        places = sorted({s.fact.place for s in sups})
        if len(places) > 1:                    # several places have one: which is meant?
            out.append(StepResult(f"where {side} leads (no place named)", "ambiguous",
                                  supports=sups, candidates=[index.shown(p) for p in places]))
            return None
        label = f"where {side} leads (no place named; the only one documented is " \
                f"{index.shown(places[0])})" if places else f"where {side} leads (no place named)"
        r = _settle(label, sups)
        out.append(r)
        return r.value if r.outcome == "value" else None
    place = resolve(a.place, index, out)
    if place is None:
        return None
    label = f"{_LABEL[a.relation]} ({index.shown(place)}" + \
        (f", {a.qualifier} side" if a.qualifier else "") + ")"
    sups = [s for s in index.by_place.get((a.relation, place), [])
            if not a.qualifier or s.fact.qualifier == a.qualifier]
    r = _settle(label, sups)
    out.append(r)
    return r.value if r.outcome == "value" else None


@dataclass
class Reasoned:
    status: str            # established | absent | unverified | missing | conflicting | ambiguous
    answer: Optional[str]  # established answer only
    possible: Optional[str]
    steps: List[StepResult]
    audience: str          # shared | personal
    explanation: List[str]


def answer(a: Ask, index: FactIndex) -> Reasoned:
    steps: List[StepResult] = []
    resolve(a, index, steps)
    last = steps[-1]
    used = [s for st in steps for s in st.supports] if last.outcome in ("value", "absent") else []
    audience = "shared" if used and all(s.scope == "shared" for s in used) else "personal"
    lines = []
    for n, st in enumerate(steps, 1):
        if st.outcome in ("value", "absent"):
            for s in st.supports:
                lines.append(f"step {n}: {st.ask}: \"{s.text}\" [{s.status}, claim {s.claim_id}]")
            for s in st.also_unverified:
                lines.append(f"    also stated, unverified (not used as evidence): \"{s.text}\" "
                             f"[{s.status}, claim {s.claim_id}]")
        elif st.outcome == "missing":
            lines.append(f"step {n}: {st.ask}: nothing documents this - cannot establish "
                         "(missing information is not proof that there is none)")
        elif st.outcome == "conflicting":
            lines.append(f"step {n}: {st.ask}: conflicting claims: " + " / ".join(st.candidates))
            for s in st.supports:
                lines.append(f"    \"{s.text}\" [{s.status}, claim {s.claim_id}]")
        else:
            lines.append(f"step {n}: {st.ask}: ambiguous: " + " / ".join(st.candidates))
    if len(steps) > 1 and last.outcome in ("value", "absent"):
        lines.append("connected through the same place name in each step: " +
                     " -> ".join(st.display or (st.value or "none") for st in steps))
    if last.outcome in ("missing", "conflicting", "ambiguous"):
        return Reasoned(last.outcome, None, None, steps, audience, lines)
    all_verified = all(st.verified for st in steps)
    shown = "none (documented: " + last.supports[0].text + ")" if last.outcome == "absent" \
        else last.display
    if not all_verified:
        weak = [n for n, st in enumerate(steps, 1) if not st.verified]
        lines.append("not established: step(s) " + ", ".join(map(str, weak)) +
                     " rest on unverified claims")
        return Reasoned("unverified", None, shown, steps, audience, lines)
    return Reasoned("absent" if last.outcome == "absent" else "established", shown, None, steps,
                    audience, lines)
