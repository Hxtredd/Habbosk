"""Question and answer data.

Answers refer to options by their TEXT, never by position, so a learned
answer still works when the game shuffles the options.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# Question kinds (from the game's yellow category labels)
CHOICE = "choice"      # ADIVINANZA, PREGUNTA, ENCUENTRA EL ERROR, ¿QUIÉN SOY? ...
ORDER = "order"        # ORDENAR: put the options in order
PAIRS = "pairs"        # PAREJAS: match left items with right items

# Where an answer came from, strongest first. A weaker source never
# overwrites a stronger one (e.g. an AI guess never replaces what the game
# itself showed as correct).
SOURCE_RANK = {"game": 4, "user": 3, "solver": 2, "ai": 1, "search": 1}


@dataclass
class Question:
    category: str                      # e.g. "PREGUNTA", "ADIVINANZA"
    text: str                          # the actual question
    options: List[str]                 # answer choices (left column for PAIRS)
    kind: str = CHOICE
    clues: List[str] = field(default_factory=list)   # hint bullets, sequences...
    right: List[str] = field(default_factory=list)   # right column for PAIRS
    language: str = "es"
    source: str = ""                   # "library", "general", "manual" ...

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Answer:
    kind: str = CHOICE
    choice: Optional[str] = None                        # CHOICE: option text
    order: List[str] = field(default_factory=list)      # ORDER: option texts in order
    pairs: Dict[str, str] = field(default_factory=dict)  # PAIRS: left -> right
    explanation: str = ""
    source: str = "ai"                 # game / user / solver / ai / search
    verified: bool = False             # confirmed by the game or the user
    confidence: float = 0.0            # 0..1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Answer":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def summary(self) -> str:
        if self.kind == ORDER:
            return " → ".join(self.order)
        if self.kind == PAIRS:
            return " · ".join(f"{a} = {b}" for a, b in self.pairs.items())
        return self.choice or "?"
