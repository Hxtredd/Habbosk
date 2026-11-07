"""Plain data structures shared by the parser, solver and UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

LETTERS = "ABCDEFGH"


@dataclass
class Piece:
    letter: str                       # "A".."H"
    name: str = ""                    # e.g. "Culata" (display only)
    caliber: Optional[str] = None     # normalised caliber string, None = neutral
    deps: List[str] = field(default_factory=list)  # letters that must be mounted first
    confidence: float = 100.0         # min OCR confidence of the critical tokens
    raw_text: str = ""                # what OCR saw on the card (for debugging)
    notes: List[str] = field(default_factory=list)  # per-piece warnings
    bbox: Optional[Tuple[int, int, int, int]] = None  # x, y, w, h inside the panel


@dataclass
class Puzzle:
    pieces: Dict[str, Piece] = field(default_factory=dict)
    target_caliber: Optional[str] = None
    weapon: Optional[str] = None
    header_text: str = ""
    warnings: List[str] = field(default_factory=list)

    def signature(self) -> Tuple:
        """Content signature used to decide if two readings are the same puzzle."""
        return (
            self.target_caliber,
            tuple(sorted((p.letter, p.caliber, tuple(sorted(p.deps)))
                         for p in self.pieces.values())),
        )


@dataclass
class Solution:
    ok: bool                          # an order could be computed
    certain: bool                     # every check passed; safe to trust
    order: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)   # wrong-caliber pieces
    levels: List[List[str]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)   # blocking errors
    warnings: List[str] = field(default_factory=list)   # non-blocking doubts
