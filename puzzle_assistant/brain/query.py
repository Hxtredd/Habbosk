"""Understanding a question (B2): what kind of information is requested
(intent) and what it is about (known subjects / entities). Simple rules,
Spanish + English; never guesses an entity that isn't clearly named.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .text import normalize, terms

# intent -> words in a claim that show it gives that kind of information
INTENT_EVIDENCE = {
    "identifier": {"id", "identifier", "identify", "number", "numero", "codigo", "code"},
    "route": {"lead", "go", "connect", "connection", "reach", "route", "way", "path", "archway",
              "arch", "gate", "exit", "entrance", "door", "conecta", "lleva", "llega", "camino",
              "ruta", "arco", "puerta", "salida", "entrada", "walk", "taxi"},
    "person": {"caretaker", "keeper", "maintainer", "custodian", "warden", "maintain",
               "maintenance", "cuidador", "encargado", "owner", "propietario", "npc", "manage",
               "run", "work"},
    "state": {"disabled", "disabl", "broken", "offline", "inactive", "enabled", "enabl",
              "working", "active", "open", "closed", "apagado", "apagada", "roto", "rota",
              "averiado", "averiada", "abierto", "abierta", "cerrado", "cerrada"},
}
_INTENT_RULES: List[Tuple[str, str]] = [
    ("identifier", r"\b(room id|id|number|numero|identif\w*|codigo|code)\b"),
    ("route", r"\b(how (do|can) i (get|go|reach)|get to|go to|where does .* (lead|go)|lead\w*|"
              r"route|path|way to|como (llego|voy|se llega)|camino|ruta|conecta)\b"),
    ("person", r"\b(who|quien|caretaker|keeper|maintain\w*|cuidador|encargado|owner|"
               r"propietario)\b"),
    ("state", r"\b(disabled|broken|offline|open|closed|working|enabled|apagad\w*|rot[oa]|"
              r"averiad\w*|abiert\w*|cerrad\w*|funciona)\b"),
]
# words that only say WHAT kind of answer is wanted ("the NAME of", "what NUMBER")
INTENT_ONLY = {"name", "nombre", "number", "numero", "identify", "identifier"}


@dataclass
class Entity:
    name: str                 # as known (claim subject, room name, map place)
    key: str                  # normalised
    words: Tuple[str, ...]    # normalised tokens
    source: str = ""          # where the name is known from


@dataclass
class Query:
    text: str
    terms: List[str]                      # content terms (stemmed)
    intents: List[str] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)      # exactly named in the question
    ambiguous: List[Entity] = field(default_factory=list)     # partially named, several fit


def detect_intents(question: str) -> List[str]:
    n = normalize(question)
    return [name for name, rx in _INTENT_RULES if re.search(rx, n)]


def make_entities(names: Iterable[Tuple]) -> List[Entity]:
    """(name, source[, normalised name]) -> entities (multi-word names first)."""
    seen: Dict[str, Entity] = {}
    for item in names:
        name, source = item[0], item[1]
        key = item[2] if len(item) > 2 and item[2] else normalize(name)
        if len(key) < 3 or key in seen:
            continue
        seen[key] = Entity(name, key, tuple(key.split()), source)
    return sorted(seen.values(), key=lambda e: -len(e.words))


def analyze(question: str, entities: List[Entity]) -> Query:
    q = Query(question, [t for t in terms(question) if t not in INTENT_ONLY],
              detect_intents(question))
    words = normalize(question).split()
    padded = " " + " ".join(words) + " "
    used = set()
    for e in entities:                     # longest names first
        if len(e.words) < 2 and len(e.key) < 5:
            continue                       # too short to recognise safely
        if f" {e.key} " in padded and not any(e.key in u for u in used):
            q.entities.append(e)
            used.add(e.key)
    if not q.entities:
        # a distinctive word of several known names -> ambiguous, never resolved
        content = set(terms(question))
        for e in entities:
            if len(e.words) >= 2 and set(terms(e.name)) & content and \
                    len(set(terms(e.name)) & content) < len(set(terms(e.name))):
                q.ambiguous.append(e)
        if len(q.ambiguous) < 2:
            q.ambiguous = []
    return q
