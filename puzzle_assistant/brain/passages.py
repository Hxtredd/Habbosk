"""B6 passages: the exact, verifiable pieces of a Parsed text.

    for p in split(parsed):          # list of Passage, in document order
        parsed.text[p.start:p.end]   # is exactly the passage text

Only kinds in SUPPORT_KINDS ("sentence", "list_item") can ever support a
claim; headings, table rows, code and quotes are kept for reading and search
but never as evidence on their own. Raw HTML blocks produce no passage.

Sentence splitting is a single character loop (linear time, no regular
expressions that could backtrack). Standard library only.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Tuple

SUPPORT_KINDS = ("sentence", "list_item")

_TERMINATORS = ".!?…"
_CLOSERS = "\"'”’»)]"
_OPENERS = "\"'“‘«([{¿¡"
_ABBREVIATIONS = frozenset((
    "mr", "mrs", "ms", "dr", "st", "sr", "sra", "srta", "dña", "dra", "prof", "etc",
    "e.g", "i.e", "vs", "no", "nº", "approx", "aprox", "p.ej", "fig", "ca",
))
_MAX_ABBR = 8
_ASCII_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SCRIPT_CACHE = {}


@dataclass(frozen=True)
class Passage:
    kind: str          # sentence | list_item | quote_sentence | heading | table_row | code
    start: int
    end: int
    heading_path: str
    ordinal: int
    flags: tuple       # "suspicious", "mixed_script"
    sha: str           # sha256 of the passage text (utf-8)


def _script_of(ch: str) -> str:
    got = _SCRIPT_CACHE.get(ch)
    if got is None:
        try:
            first = unicodedata.name(ch).split(" ", 1)[0]
        except ValueError:
            first = ""
        got = first if first in ("LATIN", "CYRILLIC", "GREEK") else ""
        _SCRIPT_CACHE[ch] = got
    return got


def suspicious(text: str) -> tuple:
    """Flags for a text: "suspicious" (invisible / control / bidi / private-use
    characters) and "mixed_script" (a word mixing Latin with Cyrillic or Greek)."""
    flags = []
    if text.isascii():
        if _ASCII_CONTROLS.search(text):
            flags.append("suspicious")
        return tuple(flags)
    bad = False
    mixed = False
    scripts = set()
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Co", "Cs") or (cat == "Cc" and ch not in "\n\t"):
            bad = True
        if ch.isalpha():
            sc = _script_of(ch)
            if sc:
                scripts.add(sc)
                if "LATIN" in scripts and len(scripts) > 1:
                    mixed = True
        elif cat[0] != "M":
            scripts.clear()
    if bad:
        flags.append("suspicious")
    if mixed:
        flags.append("mixed_script")
    return tuple(flags)


def _is_abbreviation(text: str, dot: int, start: int) -> bool:
    """Is the '.' at index `dot` the end of an abbreviation or an initial?"""
    i = dot
    lo = max(start, dot - _MAX_ABBR)
    while i > lo and (text[i - 1].isalpha() or text[i - 1] == "."):
        i -= 1
    word = text[i:dot].lstrip(".")
    if not word:
        return False
    if i > start and text[i - 1].isalnum():
        return False       # the word is cut by the scan window: not an abbreviation
    if len(word) == 1 and word.isupper():
        return True
    return word.lower() in _ABBREVIATIONS


def _starts_sentence(ch: str) -> bool:
    return ch.isupper() or ch.isdigit() or ch in _OPENERS


def _sentence_spans(text: str, start: int, end: int) -> List[Tuple[int, int]]:
    """Sentence spans inside text[start:end]; one linear pass."""
    spans = []
    seg = start
    i = start
    while i < end:
        if text[i] not in _TERMINATORS:
            i += 1
            continue
        j = i + 1
        while j < end and text[j] in _TERMINATORS:
            j += 1
        k = j
        while k < end and text[k] in _CLOSERS:
            k += 1
        if k >= end:
            break
        if text[k].isspace():
            m = k
            while m < end and text[m].isspace():
                m += 1
            if m >= end:
                break
            if _starts_sentence(text[m]) and not (
                    j == i + 1 and text[i] == "." and k == j
                    and _is_abbreviation(text, i, seg)):
                spans.append((seg, k))
                seg = m
            i = m
            continue
        i = j
    spans.append((seg, end))
    return spans


def _trimmed(text: str, s: int, e: int):
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return (s, e) if s < e else None


def split(parsed) -> list:
    """All passages of a Parsed, in document order."""
    text = parsed.text
    out: List[Passage] = []

    def add(kind: str, s: int, e: int, path: str) -> None:
        t = _trimmed(text, s, e)
        if t is None:
            return
        s, e = t
        seg = text[s:e]
        out.append(Passage(kind, s, e, path, len(out), suspicious(seg),
                           hashlib.sha256(seg.encode("utf-8")).hexdigest()))

    for b in parsed.blocks:
        k = b.kind
        if k == "paragraph" or k == "quote":
            kind = "sentence" if k == "paragraph" else "quote_sentence"
            for s, e in _sentence_spans(text, b.start, b.end):
                add(kind, s, e, b.heading_path)
        elif k in ("list_item", "heading", "table_row", "code"):
            add(k, b.start, b.end, b.heading_path)
        # raw_html and anything unknown: no passage
    return out
