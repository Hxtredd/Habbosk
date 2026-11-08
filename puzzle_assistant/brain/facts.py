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

from .text import normalize, tokens

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


def _place(s: str) -> Optional[str]:
    s = _TRAIL.sub("", " " + s.strip()).strip()
    k = key(s)
    if not k or len(k.split()) > 6 or re.search(r"\b(?:is|are|es|son|and|y|or|o)\b", k):
        return None                 # not a plain name: no fact
    return k


def _person(s: str) -> Optional[str]:
    k = key(s)
    words = k.split()
    if not words or len(words) > 4 or any(w in ("is", "are", "es", "the", "and", "y", "no",
                                                "not", "nobody", "nadie") for w in words):
        return None
    return k


def display(text: str, norm: str) -> str:
    """The words of `norm` as written in `text` (capitals and accents kept)."""
    orig = re.findall(r"[^\W_]+", text)
    want = norm.split()
    normed = [normalize(w) for w in orig]
    for i in range(len(normed) - len(want) + 1):
        if normed[i:i + len(want)] == want:
            return " ".join(orig[i:i + len(want)])
    return norm


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
    """The one fact this claim states, or None. The first form that matches
    with plain names in its place / value wins (a form whose slots are not
    plain names - "nobody" as a person - lets the later forms try). A form
    without the place ("The north exit leads to ...") takes the claim's
    subject as the place - and gives no fact without one."""
    n = " ".join(tokens(text or ""))
    if not n:
        return None
    for relation, rx, absent in _P:
        m = rx.match(n)
        if not m:
            continue
        g = m.groupdict()
        place = _place(g["place"]) if "place" in g else (_place(subject) if subject else None)
        if place is None:
            continue
        qual = _q(g.get("q")) or _q(g.get("q2"))
        if absent:
            return Fact(relation, place, None, qual)
        raw = g["value"]
        if relation in ("leads_to",):
            value = _place(raw)
        elif relation in ("caretaker", "runs"):
            value = _person(raw)
        else:
            value = raw.strip()
        if not value:
            continue
        shown = display(text, value)
        if relation == "quest_reward":
            shown = f"{value} coins"
        return Fact(relation, place, value, qual, shown)
    return None
