"""Structured facts read from claims (B3).

A claim may state ONE fact of a small, closed set of relations, written in
one of a few exact sentence forms (English and Spanish):

  leads_to      "The north door of the Plaza Mayor leads to the Jardín ..."
                "From the Bosque Oscuro the west path leads to the ..."
                "La salida norte del Puerto Coral lleva al Faro del Norte."
                "The Jardín de la Luna connects to the Biblioteca Antigua ..."
                "The Cueva Marina can only be reached from the Faro del Sur."
  caretaker     "Marta is the caretaker of ...", "Tomás maintains ...",
                "Rafael looks after ...", "El encargado del ... es Don Tito.",
                absence: "The Sala Azul has no caretaker.",
                         "La Cueva Marina no tiene encargado."
  runs          "Capitana Irene runs the Puerto Coral."
  room_id       "The Plaza Mayor has room ID 100201.", "... tiene el ID 2104553."
                absence: "The Cementerio Viejo has no permanent room ID ..."
  quest_reward  "La misión del Faro del Norte da 30 monedas."
                "The quest in the Faro del Sur gives 50 coins."

Anything else gives NO fact - nothing is guessed. A fact has no status,
evidence or owner of its own: it is part of its claim and inherits all of
them. Names are compared only as exact normalised strings (a leading article
removed): "Faro del Norte" is never "Faro del Sur", and no two places are
connected because they appear in the same source or sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .text import normalize, polarity, tokens

RELATIONS = ("leads_to", "caretaker", "runs", "room_id", "quest_reward")

_DIRS = {"north": "north", "northern": "north", "norte": "north",
         "south": "south", "southern": "south", "sur": "south",
         "east": "east", "eastern": "east", "este": "east", "oriental": "east",
         "west": "west", "western": "west", "oeste": "west", "occidental": "west"}
DIR = "(?:" + "|".join(sorted(_DIRS, key=len, reverse=True)) + ")"
EXIT = (r"(?:doors?|exits?|paths?|stairs|staircase|gates?|archway|arch|way|salidas?|puertas?|"
        r"caminos?|escaleras?|arcos?)")
LEAD = r"(?:leads?|goes|go|connects?|takes you|lleva|conduce|va|conecta)"
CARE_NOUN = r"(?:caretaker|keeper|maintainer|custodian|warden)"
CARE_NOUN_ES = r"(?:encargad[oa]|cuidador[a]?)"
_ARTICLES = {"the", "el", "la", "los", "las", "a", "an"}
# a destination / place ends before a trailing modifier ("... through the archway")
_TRAIL = re.compile(r" (?:through|via|por|a traves|from|after|at|every|on|during|desde|"
                    r"durante|cuando|when|if|si)\b.*$")


# A claim that hedges, reports, or speaks of the past or the future does not
# state a fact ("Maybe Mira ...", "I think ...", "Mira used to ...", "will"):
# uncertain relationships cannot establish anything.
_HEDGES = re.compile(r"\b(?:maybe|perhaps|probably|possibly|presumably|apparently|reportedly|"
                     r"might|may|could|would|should|will|shall|think|thinks|believe|believes|"
                     r"guess|seems?|seemed|rumou?r|rumou?red|supposedly|allegedly|used to|"
                     r"formerly|previously|once|soon|next|later|until|"
                     r"quizas?|tal vez|probablemente|posiblemente|seguramente|creo|cree|creen|"
                     r"parece|dicen|supuestamente|antes|pronto|sera|podria|deberia|solia)\b")
# indefinite people are not names
_NOT_NAMES = {"someone", "somebody", "anyone", "anybody", "everyone", "everybody", "nobody",
              "alguien", "nadie", "todos", "cualquiera", "who", "quien"}
_CONNECTORS = {"de", "del", "la", "las", "los", "el", "van", "von", "der", "den", "da", "di",
               "du", "le", "dos", "das", "al", "y"}


@dataclass(frozen=True)
class Fact:
    relation: str
    place: str                  # normalised place the fact is about
    value: Optional[str]        # normalised value; None = documented absence
    qualifier: str = ""         # leads_to: "north" / "south" / "east" / "west" / ""
    display: str = ""           # value as written in the claim (capitals, accents)

    @property
    def absent(self) -> bool:
        return self.value is None


def key(name: str) -> str:
    """Normalised name without a leading article ("the Faro" -> "faro")."""
    words = normalize(name).split()
    while words and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


def _written(text: str, norm: str) -> List[str]:
    """The words of `norm` as written in `text` ([] if not found)."""
    orig = re.findall(r"[^\W_]+", text)
    want = norm.split()
    normed = [normalize(w) for w in orig]
    for i in range(len(normed) - len(want) + 1):
        if normed[i:i + len(want)] == want:
            return orig[i:i + len(want)]
    return []


def _place(s: str, text: str) -> Optional[str]:
    """A place name: plain words, at least one written with a capital ("the
    Plaza Mayor", "the shop in the Plaza Mayor" - not "the garden")."""
    s = _TRAIL.sub("", " " + s.strip()).strip()
    k = key(s)
    if not k or len(k.split()) > 6 or re.search(r"\b(?:is|are|es|son|and|y|or|o)\b", k):
        return None                 # not a plain name: no fact
    if not any(w[:1].isupper() for w in _written(text, k)):
        return None                 # a common noun, not a name
    return k


def _person(s: str, text: str) -> Optional[str]:
    """A person's name: every word written with a capital (connectors aside),
    not an indefinite word ("someone"), at most 4 words."""
    k = key(s)
    words = k.split()
    if not words or len(words) > 4 or any(w in _NOT_NAMES or w in ("is", "are", "es", "the", "no",
                                                                   "not") for w in words):
        return None
    written = _written(text, k)
    if not written or not all(w[:1].isupper() or w.lower() in _CONNECTORS for w in written) \
            or not written[0][:1].isupper():
        return None
    return k


def display(text: str, norm: str) -> str:
    """The words of `norm` as written in `text` (capitals and accents kept)."""
    return " ".join(_written(text, norm)) or norm


def _q(d: Optional[str]) -> str:
    return _DIRS.get((d or "").strip(), "")


# (relation, pattern, groups: place, value, qualifier, absence?)
_P: List[Tuple[str, "re.Pattern", bool]] = []


def _add(relation: str, pattern: str, absent: bool = False) -> None:
    _P.append((relation, re.compile("^" + pattern + "$"), absent))


A = r"(?:the |el |la |los |las )?"
DE = r"(?:del|de la|de los|de las|de|of the|of|from the|from)"
TO = r"(?:to the|to|into the|into|al|a la|a los|a las|a|hacia el|hacia la|hacia|con el|con la)"
# leads_to ("<SUBJ>": the place is the claim's subject - "The northern exit leads to ...")
_add("leads_to", A + rf"(?:(?P<q>{DIR}) )?{EXIT} {LEAD} {TO} (?P<value>.+)")
_add("leads_to", A + rf"{EXIT} (?:(?P<q>{DIR}) )?{LEAD} {TO} (?P<value>.+)")
_add("leads_to", A + rf"(?:(?P<q>{DIR}) )?{EXIT} {DE} (?P<place>.+?) {LEAD} {TO} (?P<value>.+)")
_add("leads_to", A + rf"{EXIT} (?:(?P<q>{DIR}) )?{DE} (?P<place>.+?) {LEAD} {TO} (?P<value>.+)")
_add("leads_to", rf"(?:from|desde) {A}(?P<place>.+?) {A}(?:(?P<q>{DIR}) )?{EXIT} "
                 rf"(?:(?P<q2>{DIR}) )?{LEAD} {TO} (?P<value>.+)")
_add("leads_to", A + rf"(?P<place>[a-z0-9 ]+?) (?:connects|conecta) (?:to|with|con) {A}(?P<value>.+)")
_add("leads_to", A + r"(?P<value>.+?) can (?:only )?be reached (?:only )?from " + A + r"(?P<place>.+)")
_add("leads_to", A + r"(?P<value>.+?) solo se (?:alcanza|llega) desde " + A + r"(?P<place>.+)")
# caretaker
_add("caretaker", rf"(?P<value>.+?) is the {CARE_NOUN} of {A}(?P<place>.+)")
_add("caretaker", rf"(?P<value>.+?) (?:maintains|maintain|takes care of|cares for) {A}(?P<place>.+)")
_add("caretaker", A + rf"(?P<place>.+?) is (?:maintained|maintain|kept|cared for) by (?P<value>.+)")
_add("caretaker", A + rf"{CARE_NOUN_ES} {DE} (?P<place>.+?) es (?P<value>.+)")
_add("caretaker", rf"(?P<value>.+?) es {A}{CARE_NOUN_ES} {DE} (?P<place>.+)")
_add("caretaker", rf"(?P<value>.+?) (?:cuida|mantiene) (?:el |la |al |a la )?(?P<place>.+)")
_add("caretaker", A + rf"(?P<place>.+?) has no {CARE_NOUN}", absent=True)
_add("caretaker", A + rf"(?P<place>.+?) no tiene (?:ningun |ninguna )?{CARE_NOUN_ES}", absent=True)
_add("caretaker", r"(?:nobody|no one|nadie) (?:maintains|maintain|cuida|is the caretaker of) "
                  + A + r"(?P<place>.+)", absent=True)
# runs
_add("runs", r"(?P<value>.+?) (?:runs|manages|dirige|gestiona) " + A + r"(?P<place>.+)")
# room_id
_add("room_id", A + r"(?P<place>.+?) has (?:permanent )?(?:room )?id (?P<value>[0-9]+)(?: .*)?")
_add("room_id", A + r"(?P<place>.+?) tiene (?:el )?(?:id|numero|codigo)(?: de sala)? "
                    r"(?P<value>[0-9]+)(?: .*)?")
_add("room_id", A + r"(?P<place>.+?) has no (?:permanent )?(?:room )?id\b.*", absent=True)
_add("room_id", A + r"(?P<place>.+?) no tiene (?:ningun )?(?:id|numero|codigo)\b.*", absent=True)
# quest_reward
_add("quest_reward", r"(?:la )?mision " + DE + r" (?P<place>.+?) da (?P<value>[0-9]+) "
                     r"(?:monedas|coins)")
_add("quest_reward", A + r"quest (?:in|of|at) " + A + r"(?P<place>.+?) gives (?P<value>[0-9]+) "
                     r"(?:coins|monedas)")
_add("quest_reward", A + r"(?P<place>.+?) quest gives (?P<value>[0-9]+) (?:coins|monedas)")


def extract(text: str, subject: str = "") -> Optional[Fact]:
    """The one fact this claim states, or None (B3's sentence forms only)."""
    return _extract_b3(text, subject)[1]


def _extract_b3(text: str, subject: str = "") -> Tuple[bool, Optional[Fact]]:
    """(decided, fact). `decided` is True when B3's rules settled the claim -
    a hedge, or one of B3's forms matched (even if it then gave no fact) - so
    no other form may be tried. The FIRST form that matches
    decides: if its place / value are not plain names, the claim states no
    fact (later forms are not tried - trying them turned run-on sentences
    into junk facts such as room_id("capitana irene"); see DEVELOPMENT_NOTES,
    "BM9"). A claim saying two things therefore states none. A form
    without the place ("The north exit leads to ...") takes the claim's
    subject as the place - and gives no fact without one."""
    n = " ".join(tokens(text or ""))
    if not n or _HEDGES.search(normalize(text)):
        return True, None           # hedged / reported / past / future: not a fact
    positive = polarity(text)
    for relation, rx, absent in _P:
        if not positive and not absent:
            continue                # a negative claim states only an explicit absence
        m = rx.match(n)
        if not m:
            continue
        g = m.groupdict()
        place = _place(g["place"], text) if "place" in g else \
            (_place(subject, subject) if subject else None)
        if place is None:
            return True, None
        qual = _q(g.get("q")) or _q(g.get("q2"))
        if absent:
            return True, Fact(relation, place, None, qual)
        raw = g["value"]
        if relation in ("leads_to",):
            value = _place(raw, text)
        elif relation in ("caretaker", "runs"):
            value = _person(raw, text)
        else:
            value = raw.strip()
        if not value:
            return True, None
        shown = display(text, value)
        if relation == "quest_reward":
            shown = f"{value} coins"
        return True, Fact(relation, place, value, qual, shown)
    return False, None


# -- relations added as data (B5) ---------------------------------------------------
@dataclass(frozen=True)
class Form:
    """A sentence form of an added relation, compiled from a template such as
    "{value} opens the {place}" (words compared normalised, like B3's forms)."""
    relation: str
    value_type: str             # place | person | number | text
    rx: "re.Pattern"


_SLOT = {"{place}": "zzslotplacezz", "{value}": "zzslotvaluezz"}


def compile_form(relation: str, value_type: str, template: str) -> Form:
    """Raises ValueError for a template that is not exactly one {place}, one
    {value} and at least one fixed word."""
    t = template or ""
    if t.count("{place}") != 1 or t.count("{value}") != 1:
        raise ValueError("a sentence form needs exactly one {place} and one {value}")
    for slot, word in _SLOT.items():
        t = t.replace(slot, f" {word} ")
    words = tokens(t)
    if not [w for w in words if w not in _SLOT.values()]:
        raise ValueError("a sentence form needs at least one fixed word")
    parts = []
    for w in words:
        if w == _SLOT["{place}"]:
            parts.append(r"(?P<place>.+?)")
        elif w == _SLOT["{value}"]:
            parts.append(r"(?P<value>.+?)")
        else:
            parts.append(re.escape(w))
    return Form(relation, value_type, re.compile("^" + " ".join(parts) + "$"))


def _typed(value_type: str, raw: str, text: str) -> Optional[str]:
    raw = raw.strip()
    if value_type == "place":
        return _place(raw, text)
    if value_type == "person":
        return _person(raw, text)
    if value_type == "number":
        return raw if re.fullmatch(r"[0-9]+", raw) else None
    words = raw.split()                 # text: a short plain value, nothing joined
    if 1 <= len(words) <= 6 and not set(words) & {"and", "or", "y", "o", "is", "are", "es",
                                                  "son", "not", "no"}:
        return raw
    return None


def extract_all(text: str, subject: str = "", forms=()) -> Optional[Fact]:
    """B3's forms first - if they decide, nothing else is tried (so B3 results
    never change); otherwise the added relations' forms, the FIRST matching
    one deciding. Hedged or negative claims state no fact here either."""
    decided, fact = _extract_b3(text, subject)
    if decided or not forms or not polarity(text):
        return fact
    n = " ".join(tokens(text or ""))
    for f in forms:
        m = f.rx.match(n)
        if not m:
            continue
        place = _place(m.group("place"), text)
        value = _typed(f.value_type, m.group("value"), text)
        if place is None or not value:
            return None
        return Fact(f.relation, place, value, "", display(text, value))
    return None
