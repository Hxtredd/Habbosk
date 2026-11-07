"""Vocabulary of the SK Brain (kept small and explicit on purpose)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# Where a claim came from (how SK learned it) - not how reliable it is.
ORIGINS = (
    "imported",    # from a guide page / document
    "observed",    # seen by SK in the game (e.g. a revealed quiz answer)
    "user",        # told by a user
    "derived",     # concluded from other claims
    "ai",          # suggested by an AI model (never verified on its own)
)

# Verification status of a claim.
STATUSES = (
    "submitted",               # recorded, nobody has looked at it yet
    "awaiting_verification",   # queued for review
    "verified",                # a reviewer accepted it on the basis of evidence
    "conflicting",             # contradicted by other information; unresolved
    "rejected",                # reviewed and found wrong / unsupported
    "outdated",                # was true, no longer is (kept for history)
)

# What a piece of evidence is. Only "direct" and "confirmation" can support
# verification; repeating a statement ("assertion") never can.
EVIDENCE_KINDS = (
    "direct",        # the source itself shows it (screenshot, video, game observation, guide text)
    "confirmation",  # an independent check of the same fact from another source
    "derived",       # follows from other claims (see claim_links)
    "assertion",     # someone said so, without supporting material
)

SOURCE_KINDS = ("guide_page", "document", "webpage", "screenshot", "video", "game_observation",
                "user_statement", "quiz_memory", "world_atlas", "import_file", "other")

# Who may see a claim. Personal is the default; nothing becomes shareable
# without an explicit, recorded action, and never when its evidence is private.
SCOPES = ("personal", "shared_candidate", "shared")


@dataclass
class Hit:
    """One search result, whichever module it came from."""
    module: str                 # "library" | "quiz_memory" | "world_atlas"
    record_id: str              # id inside that module
    text: str                   # the statement / summary
    status: str                 # one of STATUSES (mapped for adapters)
    origin: str                 # one of ORIGINS
    score: float = 0.0
    category: str = ""
    sources: List[str] = field(default_factory=list)   # human-readable source references
    detail: dict = field(default_factory=dict)


@dataclass
class BrainAnswer:
    """What ask() returns. Until the reasoning layer (B3) exists, `answered`
    is always False and `hits` shows what was found."""
    question: str
    answered: bool = False
    answer: Optional[str] = None
    basis: str = ""             # "documented" | "derived" | "insufficient"
    confidence: str = "none"    # evidence-based level, set by B3
    hits: List[Hit] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)
