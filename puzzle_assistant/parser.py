"""Turns OCR words into a structured Puzzle.

Each card is expected to contain:
  * its own letter (A-H)
  * a piece name (Culata, Gatillo, Canon, ...)
  * optionally a caliber number (12, 9, .45, 7,62 ...)
  * an arrow followed by the letters it depends on (e.g. "<- C H")

The parser is deliberately conservative: anything it is not sure about is
recorded as a note so the solver can flag the answer as uncertain.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import List, Optional, Sequence, Tuple

from .model import LETTERS, Piece, Puzzle
from .ocr import Word
from .solver import normalize_caliber

PIECE_NAMES = [
    "Culata", "Gatillo", "Cañón", "Pasador", "Empuñadura", "Armazón", "Corredera",
    "Cargador", "Martillo", "Muelle", "Resorte", "Percutor", "Mira", "Alza",
    "Guardamanos", "Guardamonte", "Cerrojo", "Recámara", "Tambor", "Seguro",
    "Silenciador", "Bocacha", "Pistón", "Biela", "Extractor", "Expulsor", "Aguja",
    "Guía", "Bloque", "Correa", "Freno", "Mecanismo", "Cuerpo", "Tornillo",
    "Perno", "Cachas", "Bomba", "Retroceso", "Visor", "Mira telescópica",
]
WEAPONS = [
    "Pistola", "Escopeta", "Rifle", "Revólver", "Fusil", "Subfusil", "Carabina",
    "Ametralladora", "Francotirador", "Metralleta", "Arma",
]

ARROW_CHARS = set("←→⟵⟶⇐⇒⇦⇨⬅➡➔➜➝➞➤↤↦«»‹›<>")
ARROW_RE = re.compile(r"(<-+|-+>|<=+|=+>|←|→|⟵|⟶|⇐|⇒|⬅|➡|➔|➜|➤|«|»|<|>)")
LETTER_LIST_RE = re.compile(r"^[A-H](?:[\s,;/+&.y\-]*[A-H])*$")
CALIBER_RE = re.compile(r"^(?:cal(?:ibre)?[.:]?)?(\.?\d{1,3}(?:[.,]\d{1,3})?)(?:mm|ga)?$", re.I)
# OCR confusions that are only safe to repair inside a dependency list
DIGIT_TO_LETTER = {"8": "B", "6": "G", "0": "D", "4": "A", "3": "B", "€": "E", "£": "E",
                   "(": "C", "¢": "C"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", strip_accents(s).lower()).strip()


def best_match(text: str, vocab: Sequence[str], cutoff: float = 0.72) -> Optional[str]:
    t = norm(text)
    if len(t) < 3:
        return None
    keys = {norm(v): v for v in vocab}
    m = difflib.get_close_matches(t, list(keys), n=1, cutoff=cutoff)
    return keys[m[0]] if m else None


def _clean(tok: str) -> str:
    return tok.strip().strip("()[]{}|:;'\"`*_")


# Arrow glyphs are often misread by OCR as junk such as "e", "“e", "«", "+".
JUNK_ARROW_RE = re.compile(r"^(?=.*[^A-Za-z0-9])[^A-Z0-9]{1,3}$|^[a-z]$")
GLUED_ARROW_RE = re.compile(r"^([^A-Z0-9]{1,3}?)([A-Hc](?:[,;/]?[A-Hc])*)[.,;]?$")
COMMON_SHORT = {"de", "la", "el", "y", "a", "en", "del", "un", "al", "e"}


def _split_arrow(text: str) -> Tuple[str, bool, str, bool]:
    """Return (before, has_arrow, after, arrow_was_misread)."""
    m = ARROW_RE.search(text)
    if m:
        return text[:m.start()], True, text[m.end():], False
    if JUNK_ARROW_RE.match(text):
        return "", True, "", True
    g = GLUED_ARROW_RE.match(text)
    if g:
        return "", True, g.group(2), True
    return text, False, "", False


def _letters_in(token: str, dep_context: bool) -> List[str]:
    t = _clean(token).replace(" ", "")
    if dep_context:
        t = "".join(DIGIT_TO_LETTER.get(c, c) for c in t)
        t = t.upper() if len(t) <= 3 and t.isalpha() else t
    if not t:
        return []
    if LETTER_LIST_RE.match(t):
        return [c for c in t if c in LETTERS]
    return []


def letter_tokens(words: List[Word]) -> List[Tuple[int, List[str], float]]:
    """Words that carry piece letters: (index, letters, fraction of the word
    width where the letters start). Used to re-verify low-confidence letters."""
    out = []
    for i, w in enumerate(words):
        text = w.text.strip()
        before, has_arrow, after, _ = _split_arrow(text)
        part = after if has_arrow and after else before
        letters = _letters_in(part, dep_context=has_arrow)
        if letters:
            start = text.find(part[:1]) if part else 0
            out.append((i, letters, max(0, start) / max(1, len(text))))
    return out


def parse_card(words: List[Word], index_hint: Optional[int] = None) -> Piece:
    """Parse the OCR words of one card. `words` must be in reading order."""
    notes: List[str] = []
    raw = " ".join(w.text for w in words)
    own_candidates: List[Tuple[Word, str]] = []
    deps: List[str] = []
    dep_confs: List[float] = []
    name_parts: List[str] = []
    cal_tokens: List[Tuple[str, float]] = []
    seen_arrow = False
    arrow_line: Optional[int] = None
    expect_cal_number = False

    for w in words:
        text = w.text.strip()
        before, has_arrow, after, misread = _split_arrow(text)
        # a misread arrow glyph drags the word confidence down although the
        # letters themselves are usually clean; compensate partially
        conf = min(100.0, w.conf + 25.0) if misread else w.conf
        if misread and "c" in after:
            conf = min(conf, 30.0)  # lowercase c read as C: must be re-verified
        # dependency context = rest of the text line that holds the arrow
        in_dep_line = arrow_line is not None and w.line == arrow_line
        parts = [(before, in_dep_line)]
        if has_arrow:
            seen_arrow = True
            arrow_line = w.line
            parts.append((after, True))
        for part, in_deps in parts:
            part = part.strip()
            if not part:
                continue
            letters = _letters_in(part, dep_context=in_deps)
            if letters:
                if in_deps:
                    deps.extend(letters)
                    dep_confs.append(conf)
                elif len(letters) == 1:
                    own_candidates.append((w, letters[0]))
                else:  # several letters without an arrow: likely deps
                    deps.extend(letters)
                    dep_confs.append(w.conf)
                    notes.append("dependency letters found without an arrow")
                continue
            low = norm(part)
            if low in ("cal", "calibre", "cal."):
                expect_cal_number = True
                continue
            m = CALIBER_RE.match(part.replace(" ", ""))
            if m and any(ch.isdigit() for ch in m.group(1)):
                cal_tokens.append((m.group(1), w.conf + (5 if expect_cal_number or low.startswith("cal") else 0)))
                expect_cal_number = False
                continue
            expect_cal_number = False
            if sum(c.isalpha() for c in part) >= 3 and not in_deps:
                name_parts.append(_clean(part))
                continue
            # Anything else that is short and unexplained may be a misread
            # letter or arrow: never ignore it silently.
            cp = _clean(part)
            if cp and (in_deps or any(c in LETTERS or c.isdigit() for c in cp)
                       or (len(cp) <= 2 and cp.lower() not in COMMON_SHORT and cp.isalpha())):
                notes.append(f"unrecognised text '{cp}'")

    # ---- own letter ------------------------------------------------------
    letter: Optional[str] = None
    conf_pool: List[float] = list(dep_confs)
    if own_candidates:
        if len(own_candidates) == 1:
            w, letter = own_candidates[0]
        else:
            # without an arrow: the biggest letter is the card's own letter,
            # the rest are its dependencies (read left-to-right)
            own_candidates.sort(key=lambda t: -t[0].h)
            biggest, second = own_candidates[0], own_candidates[1]
            if biggest[0].h >= 1.25 * second[0].h:
                w, letter = biggest
                others = own_candidates[1:]
            else:
                ordered = sorted(own_candidates, key=lambda t: (t[0].line, t[0].x))
                w, letter = ordered[0]
                others = ordered[1:]
                notes.append("own letter chosen by position (ambiguous)")
            for ow, ol in others:
                deps.append(ol)
                conf_pool.append(ow.conf)
            if not seen_arrow:
                notes.append("dependencies read without an arrow symbol")
        conf_pool.append(w.conf)
    if seen_arrow and not deps:
        notes.append("arrow found but no target letter read")

    # ---- caliber ----------------------------------------------------------
    caliber: Optional[str] = None
    if cal_tokens:
        cal_tokens.sort(key=lambda t: -t[1])
        caliber = normalize_caliber(cal_tokens[0][0])
        conf_pool.append(cal_tokens[0][1])
        distinct = {normalize_caliber(c) for c, _ in cal_tokens}
        if len(distinct) > 1:
            notes.append("several numbers on card: " + ", ".join(sorted(distinct)))

    # ---- name (display only, never affects the solution) -----------------
    raw_name = " ".join(name_parts)
    name = best_match(raw_name, PIECE_NAMES) or raw_name

    # de-duplicate deps preserving order
    seen = set()
    deps = [d for d in deps if not (d in seen or seen.add(d))]

    piece = Piece(letter=letter or "?", name=name, caliber=caliber, deps=deps,
                  confidence=min(conf_pool) if conf_pool else 0.0,
                  raw_text=raw, notes=notes)
    return piece


def parse_header(words: List[Word]) -> Tuple[Optional[str], Optional[str], List[str], str]:
    """Return (target_caliber, weapon, notes, header_text)."""
    notes: List[str] = []
    text = " ".join(w.text for w in words)
    flat = strip_accents(text).lower()
    target: Optional[str] = None
    m = re.search(r"cal(?:ibre|\.)?\s*[:.]?\s*(\.?\d{1,3}(?:[.,]\d{1,3})?)", flat)
    if m:
        target = normalize_caliber(m.group(1))
    else:
        # numbers that are not part of the countdown timer
        nums = []
        toks = [w.text for w in words]
        for i, t in enumerate(toks):
            tt = t.strip().lower()
            if ":" in tt:
                continue
            nxt = toks[i + 1].lower() if i + 1 < len(toks) else ""
            if re.fullmatch(r"\d{1,3}(?:[.,]\d{1,3})?(mm)?", tt) and not nxt.startswith(("s", "seg")):
                nums.append(normalize_caliber(tt))
        nums = sorted(set(n for n in nums if n))
        if len(nums) == 1:
            target = nums[0]
            notes.append("target caliber inferred (no 'calibre' label read)")
        elif len(nums) > 1:
            notes.append("several candidate target calibers: " + ", ".join(nums))
    weapon = None
    for w in words:
        m2 = best_match(w.text, WEAPONS, cutoff=0.8)
        if m2 and m2 != "Arma":
            weapon = m2
            break
    return target, weapon, notes, text


def assign_letters(pieces: List[Piece], expected: int) -> List[str]:
    """Resolve each card's letter, falling back to reading order when OCR failed."""
    notes: List[str] = []
    positional = list(LETTERS[:expected])
    read = [p.letter for p in pieces]
    valid_read = [l for l in read if l in LETTERS]
    if len(pieces) == expected and sorted(valid_read) == positional:
        if read != positional:
            notes.append("cards are not in A-H reading order (using printed letters)")
        return notes
    taken = {l for l in read if l in LETTERS and read.count(l) == 1}
    for i, p in enumerate(pieces):
        if p.letter in LETTERS and read.count(p.letter) == 1:
            continue
        guess = positional[i] if i < len(positional) else None
        if guess and guess not in taken:
            p.notes.append(f"letter not read clearly, assumed {guess} from position")
            p.letter = guess
            taken.add(guess)
        else:
            p.notes.append("letter could not be determined")
    return notes


def parse_total(words: List[Word]) -> Optional[int]:
    """'Montadas 0/8' -> 8 (number of pieces in this puzzle)."""
    flat = strip_accents(" ".join(w.text for w in words)).lower()
    m = re.search(r"montad\w*\s*\d+\s*/\s*(\d+)", flat)
    return int(m.group(1)) if m else None


def finish_header(puzzle: Puzzle, header_words: List[Word], n_pieces: int) -> None:
    target, weapon, notes, text = parse_header(header_words)
    puzzle.target_caliber, puzzle.weapon, puzzle.header_text = target, weapon, text
    puzzle.warnings.extend(notes)
    total = parse_total(header_words)
    if total is not None and total != n_pieces:
        puzzle.warnings.append(f"game says {total} pieces but {n_pieces} were found")
        puzzle.expected = total
    else:
        puzzle.expected = n_pieces


def build_from_readings(readings, names: List[str], header_words: List[Word]) -> Puzzle:
    """Puzzle from cardreader.CardReading objects (cards in reading order)."""
    puzzle = Puzzle()
    for i, r in enumerate(readings):
        letter = LETTERS[i]  # cards are laid out A, B, C... in reading order
        notes = list(r.notes)
        if r.badge is None:
            notes.append("badge letter not confirmed")
        elif r.badge != letter:
            notes.append(f"badge reads {r.badge} but position says {letter}")
        raw_name = names[i] if i < len(names) else ""
        puzzle.pieces[letter] = Piece(
            letter=letter, name=best_match(raw_name, PIECE_NAMES) or raw_name,
            caliber=r.caliber, deps=list(dict.fromkeys(r.deps)), confidence=r.confidence,
            raw_text=r.debug, notes=notes, mounted=r.mounted)
    return puzzle


def build_puzzle(card_words: List[List[Word]], header_words: List[Word],
                 expected: int = 0) -> Puzzle:
    expected = expected or len(card_words)
    pieces = [parse_card(ws, i) for i, ws in enumerate(card_words)]
    puzzle = Puzzle()
    puzzle.warnings.extend(assign_letters(pieces, expected))
    for i, p in enumerate(pieces):
        if p.letter in puzzle.pieces:
            puzzle.warnings.append(f"letter {p.letter} appears twice")
            continue
        if p.letter in LETTERS:
            puzzle.pieces[p.letter] = p
    finish_header(puzzle, header_words, expected)
    return puzzle


def cluster_by_anchors(words: List[Word], expected: int = 8
                       ) -> Optional[Tuple[List[List[Word]], List[Word]]]:
    """Fallback when card boxes cannot be found: group words around piece names."""
    anchors = [w for w in words if best_match(w.text, PIECE_NAMES, cutoff=0.8)]
    if (expected and len(anchors) != expected) or not 3 <= len(anchors) <= len(LETTERS):
        return None
    # reading order of anchors
    med_h = sorted(a.h for a in anchors)[len(anchors) // 2] or 10
    anchors.sort(key=lambda a: (round(a.cy / (med_h * 3)), a.cx))
    dists = []
    for a in anchors:
        d = min(((a.cx - b.cx) ** 2 + (a.cy - b.cy) ** 2) ** 0.5 for b in anchors if b is not a)
        dists.append(d)
    radius = 0.6 * sorted(dists)[len(dists) // 2]
    groups: List[List[Word]] = [[a] for a in anchors]
    header: List[Word] = []
    # Text rows above every piece name that mention the weapon or "calibre"
    # (e.g. "Pistola - Calibre 12") belong to the header, not to a piece.
    top = min(a.y for a in anchors)
    header_lines = set()
    for w in words:
        if w.y + w.h < top and (norm(w.text).startswith("calibre")
                                or best_match(w.text, WEAPONS, cutoff=0.8)):
            header_lines.add(w.line)
    for w in words:
        if any(w is a for a in anchors):
            continue
        if w.line in header_lines:
            header.append(w)
            continue
        best_i, best_d = -1, 1e18
        for i, a in enumerate(anchors):
            d = ((w.cx - a.cx) ** 2 + (w.cy - a.cy) ** 2) ** 0.5
            if d < best_d:
                best_i, best_d = i, d
        if best_d <= radius:
            groups[best_i].append(w)
        else:
            header.append(w)
    for g in groups:
        g.sort(key=lambda w: (round(w.cy / med_h), w.x))
    return groups, header
