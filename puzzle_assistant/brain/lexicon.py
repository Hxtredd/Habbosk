"""Curated synonyms (B2). Small on purpose: every group is a set of words
that mean the same thing in a question about places, people, objects or
rules. A synonym match counts less than the exact word (SYNONYM_WEIGHT).
Spanish and English. Other domains can add their own groups later.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List

from .text import stem

SYNONYM_WEIGHT = 0.7

_GROUPS: List[List[str]] = [
    # identifiers
    ["id", "identifier", "identify", "number", "numero", "codigo", "code", "num"],
    # people who look after something
    ["caretaker", "keeper", "maintainer", "custodian", "warden", "cuidador", "cuidadora",
     "encargado", "encargada", "conserje", "guardian", "responsible", "responsable",
     "maintain", "maintenance", "mantiene",
     "mantener", "mantenimiento", "cuida", "cuidar", "care"],
    # working / not working
    ["disabled", "disabl", "broken", "offline", "inactive", "apagado", "apagada", "roto", "rota",
     "averiado", "averiada", "desactivado", "desactivada", "inactivo", "inactiva"],
    ["enabled", "enabl", "working", "active", "activo", "activa", "encendido", "encendida",
     "funciona"],
    ["open", "abierto", "abierta", "abre"],
    ["closed", "cerrado", "cerrada", "cierra"],
    # directions
    ["north", "northern", "norte"],
    ["south", "southern", "sur"],
    ["east", "eastern", "este", "oriental"],
    ["west", "western", "oeste", "occidental"],
    # navigation
    ["lead", "go", "connect", "connection", "reach", "conecta", "lleva", "llega", "conduce"],
    ["route", "way", "path", "camino", "ruta"],
    ["archway", "arch", "arco", "gate", "gateway", "doorway", "puerta"],
    ["exit", "salida", "door"],
    ["entrance", "entrada"],
    # places
    ["room", "sala", "habitacion", "place", "lugar", "area"],
    ["garden", "jardin"],
    ["courtyard", "patio"],
    ["fountain", "fuente"],
    ["street", "calle", "avenue", "avenida"],
    ["shop", "store", "tienda"],
    # things
    ["item", "object", "objeto", "furni", "mueble"],
    ["quest", "mission", "mision", "task", "tarea"],
    ["rule", "regla", "norma"],
    ["price", "cost", "precio", "cuesta", "coste"],
]

_MAP: Dict[str, FrozenSet[str]] = {}
for _g in _GROUPS:
    _s = frozenset(stem(w) for w in _g)
    for _w in _s:
        _MAP[_w] = _MAP.get(_w, frozenset()) | _s


def synonyms(term: str) -> FrozenSet[str]:
    """Other stemmed words meaning the same as `term` (not including it)."""
    return _MAP.get(term, frozenset()) - {term}


def same_group(a: str, b: str) -> bool:
    return a == b or b in _MAP.get(a, frozenset())
