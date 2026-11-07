"""Text helpers: normalisation for matching, a light language guess."""
from __future__ import annotations

import re
import unicodedata

_SPANISH_WORDS = {
    "que", "qué", "cual", "cuál", "cuales", "cuáles", "es", "el", "la", "los", "las", "de",
    "del", "un", "una", "y", "con", "por", "para", "sigue", "frase", "tiene", "error",
    "quien", "quién", "soy", "cada", "une", "ordena", "menor", "mayor", "cuantos", "cuántos",
}
_ENGLISH_WORDS = {
    "what", "which", "who", "is", "the", "of", "a", "an", "and", "with", "for", "next",
    "comes", "sentence", "has", "error", "am", "each", "match", "order", "smallest",
    "largest", "how", "many", "true", "false", "not",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(s: str) -> str:
    """Lower-case, no accents, punctuation removed, single spaces.
    'Sigue la letra:  ¿V, W?'  ->  'sigue la letra v w'"""
    s = strip_accents(s or "").lower()
    s = s.translate(str.maketrans({"−": "-", "–": "-", "—": "-", "×": "*", "÷": "/"}))
    s = re.sub(r"[^a-z0-9+\-*/=<>%]+", " ", s)
    s = re.sub(r"\s*([+\-*/=<>%])\s*", r"\1", s)        # '63 - 8' == '63-8'
    return re.sub(r"\s+", " ", s).strip()


def guess_language(s: str) -> str:
    """'es' or 'en' (best guess; Spanish when unsure, the game is mostly Spanish)."""
    if any(c in s for c in "¿¡ñÑ"):
        return "es"
    words = set(re.findall(r"[a-záéíóúüñ]+", (s or "").lower()))
    es = len(words & _SPANISH_WORDS)
    en = len(words & _ENGLISH_WORDS)
    return "en" if en > es else "es"
