"""Dependency-graph solver (Kahn's topological sort).

Rules (in-game: "Monta cada pieza cuando estén puestas las que indica su
flecha. Aparta las de otro calibre y prueba el arma al acabar."), confirmed
by the player:

* A piece may be mounted only after every piece its arrow points to
  (card A "↳ B H" = B and H first). Clicking too early costs a point.
* Pieces whose caliber differs from the target caliber are left alone.
* Pieces with no caliber ("–") are caliber-neutral and are mounted.
* When several pieces are possible, any order is accepted.
* After the last piece, press "Probar".

Mounted pieces (green, ✓) are already accepted by the game: their own
caliber and arrows no longer matter, so a faded/misread mounted card never
affects the answer. Only the remaining pieces are ordered.

The solver never adds or removes dependencies. Anything that does not add up
is reported instead of guessed.
"""
from __future__ import annotations

import heapq
from typing import Dict, List

from .model import LETTERS, Puzzle, Solution


def normalize_caliber(text: str | None) -> str | None:
    """'12', 'Cal. 12', '9mm', '.45', '7,62' -> canonical string."""
    if text is None:
        return None
    t = str(text).strip().lower().replace(",", ".")
    for junk in ("calibre", "cal.", "cal", "mm", "ga", "º", "#"):
        t = t.replace(junk, "")
    t = t.strip().lstrip(".").strip()
    if not t:
        return None
    try:
        f = float(t)
    except ValueError:
        return t
    return str(int(f)) if f.is_integer() else ("%g" % f)


def solve(puzzle: Puzzle, expected_pieces: int = 8, min_confidence: float = 60.0) -> Solution:
    sol = Solution(ok=False, certain=False)
    sol.warnings.extend(puzzle.warnings)
    pieces = puzzle.pieces
    done = {l for l, p in pieces.items() if p.mounted}
    doubtful = set()

    # --- sanity checks on the reading -----------------------------------
    expected_letters = set(LETTERS[:expected_pieces])
    found = set(pieces)
    if found != expected_letters:
        missing = sorted(expected_letters - found)
        extra = sorted(found - expected_letters)
        if missing:
            sol.problems.append("Missing pieces: " + ", ".join(missing))
        if extra:
            sol.problems.append("Unexpected pieces: " + ", ".join(extra))

    target = normalize_caliber(puzzle.target_caliber)
    if target is None:
        sol.problems.append("Target caliber not read")

    for p in pieces.values():
        if p.mounted:
            continue  # already accepted by the game; its reading is irrelevant
        for d in p.deps:
            if d == p.letter:
                sol.problems.append(f"{p.letter} depends on itself (misread?)")
                doubtful.add(p.letter)
            elif d not in pieces:
                sol.problems.append(f"{p.letter} depends on unknown piece {d}")
                doubtful.add(p.letter)
        if p.confidence < min_confidence:
            sol.warnings.append(f"Card {p.letter} not read clearly")
            doubtful.add(p.letter)
        for n in p.notes:
            sol.warnings.append(f"{p.letter}: {n}")
            doubtful.add(p.letter)

    # --- caliber filtering ----------------------------------------------
    included: Dict[str, List[str]] = {}
    for letter in sorted(pieces):
        p = pieces[letter]
        cal = normalize_caliber(p.caliber)
        if p.mounted:
            included[letter] = []
        elif target is not None and cal is not None and cal != target:
            sol.excluded.append(letter)
        else:
            # prerequisites already mounted are satisfied
            included[letter] = sorted(set(d for d in p.deps if d != letter and d not in done))

    if target is not None:
        cals = {normalize_caliber(p.caliber) for p in pieces.values() if p.caliber}
        if cals and target not in cals and not done:
            sol.warnings.append(f"No piece shows target caliber {target}")

    for letter, deps in included.items():
        for d in deps:
            if d in sol.excluded:
                sol.problems.append(f"{letter} needs {d}, but {d} has another caliber")
                doubtful.update((letter, d))

    # Consistency of the game's ✓ marks with a trusted reading: a piece can only
    # have been mounted after its prerequisites, and never if it is excluded.
    for letter in sorted(done):
        p = pieces[letter]
        if p.confidence < min_confidence or p.notes:
            continue  # faded card, reading not trusted: nothing to check
        cal = normalize_caliber(p.caliber)
        if target is not None and cal is not None and cal != target:
            sol.warnings.append(f"{letter} is mounted but was read as caliber {cal}")
            doubtful.add(letter)
        for d in p.deps:
            if d in pieces and d not in done:
                sol.warnings.append(f"{letter} is mounted before {d}: reading may be wrong")
                doubtful.update((letter, d))

    # --- Kahn's algorithm (alphabetical tie-break => deterministic) -------
    todo = {n for n in included if n not in done}
    indeg = {n: 0 for n in todo}
    children: Dict[str, List[str]] = {n: [] for n in todo}
    for n in todo:
        for d in included[n]:
            if d in todo:
                indeg[n] += 1
                children[d].append(n)

    heap = [n for n in todo if indeg[n] == 0]
    heapq.heapify(heap)
    order: List[str] = []
    while heap:
        n = heapq.heappop(heap)
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                heapq.heappush(heap, c)

    sol.done = sorted(done)
    sol.doubtful = sorted(doubtful)
    if len(order) != len(todo):
        stuck = sorted(n for n in todo if n not in order)
        sol.problems.append("Circular dependency between: " + ", ".join(stuck))
        sol.doubtful = sorted(doubtful | set(stuck))
        sol.remaining = order
        sol.order = sol.done + order
        return sol

    level_of: Dict[str, int] = {}
    for n in order:
        deps = [d for d in included[n] if d in todo]
        level_of[n] = 0 if not deps else 1 + max(level_of[d] for d in deps)
    if level_of:
        levels: List[List[str]] = [[] for _ in range(max(level_of.values()) + 1)]
        for n in order:
            levels[level_of[n]].append(n)
        sol.levels = levels

    sol.remaining = order
    sol.order = sol.done + order
    sol.ok = bool(sol.order) and not sol.problems
    sol.certain = sol.ok and not sol.warnings
    return sol
