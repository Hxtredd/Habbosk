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
    """What ask() returns. `answered` is True only for an ESTABLISHED answer
    (every claim used is verified) - see brain/reason.py. Otherwise `status`
    says why not, `possible` may hold an unverified possibility (never an
    answer) and `hits` shows what search found."""
    question: str
    answered: bool = False
    answer: Optional[str] = None
    basis: str = ""             # "documented" | "derived" | "insufficient"
    confidence: str = "none"    # "verified" for answers; else "unverified" / "none"
    hits: List[Hit] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)
    # B3
    status: str = "not_reasoned"   # established | absent | unverified | missing |
    #                                conflicting | ambiguous | not_reasoned
    possible: Optional[str] = None
    steps: List[dict] = field(default_factory=list)   # each: ask, outcome, value, claims
    audience: str = ""          # "shared" only if every claim used is shared


# -- B4: answer categories -----------------------------------------------------------
# Every answer from Brain.respond() is in exactly ONE category. Only an
# Established answer may be stated without a qualifier.
CATEGORIES = (
    "established",    # every fact used is verified (incl. a verified "there is none")
    "documented",     # an unverified fact whose cited evidence quote states it by itself
    "ai_generated",   # defined for later milestones - never produced before the AI gateway
    "unknown",        # anything else, always with a reason
)
UNKNOWN_REASONS = (
    "nothing_documented",   # nothing the asker may see documents it (never "there is none")
    "sources_disagree",     # the records disagree - each side is shown, labelled
    "ambiguous",            # several places / people fit - SK asks which one
    "only_outdated",        # only information marked outdated
    "unsupported",          # only statements without a source that states them
    "not_understood",       # not a question SK can reason about (yet)
    "did_you_mean",         # B5.1: a name with a small typo - SK asks before answering
    "evidence_withdrawn",   # B5.2: a source behind it was withdrawn by its owner - awaiting review
)


@dataclass
class Citation:
    """One claim behind an answer, with the evidence the asker may see."""
    claim_id: int
    text: str
    status: str
    scope: str
    evidence: List[dict] = field(default_factory=list)   # kind, quote, source title/kind/date
    supports: bool = False      # Documented only: this claim's quote states the fact by itself


@dataclass
class Response:
    """What Brain.respond() returns (B4). Read-only: producing it never
    changes anything. `save_offer` stays None until confirmed saving exists
    (B7); `ai` stays "none" until the AI gateway exists (B8)."""
    question: str
    language: str                       # "en" | "es"
    category: str                       # one of CATEGORIES
    reason: str = ""                    # unknown only: one of UNKNOWN_REASONS
    value: Optional[str] = None         # the answer (established / documented)
    sentence: str = ""                  # ready-made wording, in `language`
    citations: List[Citation] = field(default_factory=list)
    steps: List[dict] = field(default_factory=list)          # the reasoning (from ask())
    clarification: Optional[dict] = None   # ambiguous: {"question": ..., "options": [...]}
    sides: List[dict] = field(default_factory=list)          # sources_disagree: value + claims
    possible: Optional[str] = None      # unknown: an unverified / outdated possibility, labelled
    related: List[dict] = field(default_factory=list)        # labelled search results - never answers
    # "shared" only if every claim used is shared. It covers the ANSWER (value,
    # sentence, possible, citations, steps); `related` is always the asker's own view
    # and must never be passed on with a shared answer.
    audience: str = ""
    ai: str = "none"
    save_offer: Optional[dict] = None
    b3_status: str = ""                 # ask()'s own status, for comparison
    # B5.1: how a question outside B3's exact grammar was understood
    understood_as: str = ""             # shown with the answer ("Understood as: ...")
    interpretation: Optional[dict] = None   # relation, entity, corrections (for "why")
