"""Text handling for retrieval (B2): normalisation, tokens, light stemming,
stop words. Spanish + English; nothing here is specific to Habbo.

The SAME functions produce the words stored in the search index and the
words of a question, so the full-text (FTS5) path and the plain-Python
path see exactly the same terms.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List

# Bump when tokenising / stemming / synonyms change: the stored index is
# rebuilt automatically (it is derived data; claims are never touched).
INDEX_VERSION = 3   # lexicon-only changes don't need a rebuild (query-side)

STOP = set("""
a al algo algun alguna alguno algunos ante antes aqui asi con como contra cual cuales cuando
de del desde donde dos el ella ellas ello ellos en entre era eres es esa ese eso esta estan
estar este esto estos ha hace hacia han hay hasta la las le les lo los mas me mi mis mucho muy
nada nos o otra otro para pero poco por porque que quien quienes se sea segun ser si
sobre son su sus tambien te tiene tienen todo tu tus un una uno unos unas y ya yo
about above after again all also am an and any are as at be been before being both but by
can could did do does doing done for from get gets got had has have having he her here hers
him his how i if in into is it its itself just me more most my of on once only or
other our out over own same she should so some such than that the their them then there
these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours
please tell know find show give called named
""".split())


# Negation is NOT discarded: it sets the polarity of a sentence (see
# polarity()); the words themselves are kept out of the search terms so
# "not" doesn't match every other negative sentence.
NEGATIONS = {"not", "no", "never", "nor", "neither", "none", "nobody", "nothing", "cannot",
             "nunca", "jamas", "ni", "ningun", "ninguna", "ninguno", "nadie", "tampoco", "sin"}
_CONTRACTION_STEMS = {"isn", "aren", "wasn", "weren", "doesn", "don", "didn", "hasn", "haven",
                      "hadn", "won", "wouldn", "shouldn", "couldn", "can", "mustn", "needn",
                      "ain"}


def polarity(text: str) -> bool:
    """False if the sentence is negated ("is not", "isn't", "no está",
    "never", "nunca"...), True otherwise. Two negations count as one (rare;
    kept simple and conservative)."""
    toks = normalize(text).split()
    for i, t in enumerate(toks):
        if t in NEGATIONS:
            return False
        if t == "t" and i > 0 and toks[i - 1] in _CONTRACTION_STEMS:
            return False
        if t == "cant":
            return False
    return True


def normalize(text: str) -> str:
    """Lower case, accents removed, punctuation -> spaces."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def stem(w: str) -> str:
    """Very light, predictable stemming (plurals, -ing, -ed) for es / en.
    Numbers are kept exactly."""
    if w.isdigit() or len(w) <= 3:
        return w
    for suf, rep, minlen in (("ies", "y", 5), ("ches", "ch", 6), ("shes", "sh", 6),
                             ("sses", "ss", 6), ("ing", "", 6), ("ed", "", 5),
                             ("ones", "on", 6), ("es", "", 5), ("s", "", 4)):
        if w.endswith(suf) and len(w) >= minlen and not w.endswith("ss"):
            return w[:len(w) - len(suf)] + rep
    return w


def tokens(text: str) -> List[str]:
    """All normalised words, in order (numbers and short words kept)."""
    return normalize(text).split()


def terms(text: str) -> List[str]:
    """Content terms: stemmed, without stop words and negation words (those
    go into polarity()). Numbers of any length and short identifiers such
    as 'id' are kept; contraction fragments ("isn" + "t") are dropped."""
    toks = tokens(text)
    out = []
    for i, t in enumerate(toks):
        if t in STOP or t in NEGATIONS:
            continue
        if t == "t" or (t in _CONTRACTION_STEMS and i + 1 < len(toks) and toks[i + 1] == "t"):
            continue
        out.append(stem(t))
    return out


def index_text(*parts: str) -> str:
    """Space-separated terms stored in the index (one 'document')."""
    out: List[str] = []
    for p in parts:
        out += terms(p or "")
    return " ".join(out)
