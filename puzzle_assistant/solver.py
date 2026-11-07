"""Dependency-graph solver (Kahn's topological sort).

Rules implemented (from the in-game instructions
"Monta cada pieza cuando esten puestas las que indica su flecha. Aparta las de
otro calibre y prueba el arma al acabar."):

* A piece may be mounted only after every piece its arrow points to.
* Pieces whose caliber differs from the target caliber are set aside.
* Pieces with no caliber printed are caliber-neutral and are mounted.
* After the last piece, the weapon is tested.

The solver never adds or removes dependencies. If something does not add up
(unknown letters, cycles, a mounted piece that depends on a set-aside piece,
missing target caliber) it reports a problem instead of guessing.
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
        for d in p.deps:
            if d == p.letter:
                sol.problems.append(f"{p.letter} depends on itself (OCR error?)")
            elif d not in pieces:
                sol.problems.append(f"{p.letter} depends on unknown piece {d}")
        if p.confidence < min_confidence:
            sol.warnings.append(f"Low OCR confidence on {p.letter} ({p.confidence:.0f})")
        for n in p.notes:
            sol.warnings.append(f"{p.letter}: {n}")

    # --- caliber filtering ----------------------------------------------
    included: Dict[str, List[str]] = {}
    for letter in sorted(pieces):
        p = pieces[letter]
        cal = normalize_caliber(p.caliber)
        if target is not None and cal is not None and cal != target:
            sol.excluded.append(letter)
        else:
            included[letter] = sorted(set(d for d in p.deps if d != letter))

    if target is not None:
        cals = {normalize_caliber(p.caliber) for p in pieces.values() if p.caliber}
        if cals and target not in cals:
            sol.warnings.append(f"No piece shows target caliber {target}")

    for letter, deps in included.items():
        for d in deps:
            if d in sol.excluded:
                sol.problems.append(
                    f"{letter} needs {d}, but {d} has another caliber")

    # --- Kahn's algorithm (alphabetical tie-break => deterministic) -------
    graph_nodes = set(included)
    indeg = {n: 0 for n in graph_nodes}
    children: Dict[str, List[str]] = {n: [] for n in graph_nodes}
    for n, deps in included.items():
        for d in deps:
            if d in graph_nodes:
                indeg[n] += 1
                children[d].append(n)

    heap = [n for n in graph_nodes if indeg[n] == 0]
    heapq.heapify(heap)
    order: List[str] = []
    while heap:
        n = heapq.heappop(heap)
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                heapq.heappush(heap, c)

    if len(order) != len(graph_nodes):
        stuck = sorted(n for n in graph_nodes if n not in order)
        sol.problems.append("Circular dependency between: " + ", ".join(stuck))
        sol.order = order
        return sol

    # Parallel "levels" (pieces that could be mounted in any order together)
    level_of: Dict[str, int] = {}
    for n in order:
        deps = [d for d in included[n] if d in graph_nodes]
        level_of[n] = 0 if not deps else 1 + max(level_of[d] for d in deps)
    if level_of:
        levels: List[List[str]] = [[] for _ in range(max(level_of.values()) + 1)]
        for n in order:
            levels[level_of[n]].append(n)
        sol.levels = levels

    sol.order = order
    sol.ok = bool(order) and not sol.problems
    sol.certain = sol.ok and not sol.warnings
    return sol
