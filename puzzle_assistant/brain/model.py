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

# Who may see a claim:
#   personal          - only its owner, in every API call;
#   shared_candidate  - proposed for the shared library: still only its owner
#                       (and moderators reviewing it) until approved;
#   shared            - everyone, after a moderator approved it. Its status
#                       still says whether it is verified.
# Nothing becomes shareable without an explicit, recorded action, and never
# when its evidence comes from a private source.
SCOPES = ("personal", "shared_candidate", "shared")

# People who may approve shared knowledge (future community library).
MODERATOR_KINDS = ("moderator",)
# Automatic contributors: may record, never verify on their own - except a
# direct game observation recorded by SK itself (see store.review).
AUTOMATIC_KINDS = ("system", "importer")

STATUS_LABELS = {
    "verified": "Verified",
    "submitted": "Unverified (not checked yet)",
    "awaiting_verification": "Unverified (waiting for review)",
    "conflicting": "Conflicting information - unresolved",
    "rejected": "Rejected",
    "outdated": "Outdated (no longer true)",
}


def is_established(status: str) -> bool:
    """Only verified knowledge may be presented as an established fact."""
    return status == "verified"


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

    @property
    def established(self) -> bool:
        """True only for verified knowledge (everything else must be shown
        as unverified, conflicting, outdated...)."""
        return is_established(self.status)

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def display(self) -> str:
        """Text safe to show to a person: unverified knowledge is never
        shown without its status."""
        return self.text if self.established else f"[{self.label}] {self.text}"


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
