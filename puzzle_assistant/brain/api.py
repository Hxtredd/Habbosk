"""The SK Brain's public API - the one entry point for every module.

    from puzzle_assistant.brain import Brain
    brain = Brain()                       # %APPDATA%\\SK Assistant\\knowledge.db
    hits = brain.search("¿Dónde está el Gran Casino?")
    cid = brain.learn("El Gran Casino está junto al Spa.", origin="imported",
                      subject="Gran Casino", category="lugares")
    brain.add_evidence(cid, "direct", source_id=..., locator="...")
    brain.review(cid, "verified", note="matches the map screenshot")

All calls are synchronous and UI-free: the application decides whether to
run them in a background thread. Nothing here imports Tkinter.

Isolation: every reading call takes `viewer` (default: the local user).
People only ever see shared knowledge plus their own. Results carry their
status; `Hit.display()` never shows unverified knowledge without saying so,
and `Hit.established` is True only for verified knowledge.

Stage status: B1 = storage, provenance, statuses, versions, adapters and a
basic word-overlap search. Ranked retrieval (B2), the reasoning that turns
hits into an answer (B3), the page importer (B5) and the test bench (B6) are
added behind these same methods.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .adapters import AtlasAdapter, QuizMemoryAdapter, overlap_score
from .model import BrainAnswer, Hit
from .store import BrainError, KnowledgeStore

__all__ = ["Brain", "BrainError"]


def _default_paths():
    from ..config import DATA_DIR
    return (os.path.join(DATA_DIR, "knowledge.db"), os.path.join(DATA_DIR, "habbosk.db"),
            os.path.join(DATA_DIR, "atlas.db"))


class Brain:
    def __init__(self, knowledge_path: Optional[str] = None, quiz_path: Optional[str] = None,
                 atlas_path: Optional[str] = None):
        k, q, a = _default_paths() if knowledge_path is None else (knowledge_path, None, None)
        self.store = KnowledgeStore(knowledge_path or k)
        # other modules, read-only (never created, migrated or modified here)
        self.sources = [QuizMemoryAdapter(quiz_path if quiz_path is not None else (q or "")),
                        AtlasAdapter(atlas_path if atlas_path is not None else (a or ""))]

    def close(self) -> None:
        self.store.close()
        for s in self.sources:
            s.close()

    # -- who --------------------------------------------------------------------
    @property
    def local_user(self) -> str:
        return self.store.local_user

    # -- adding knowledge -----------------------------------------------------------
    def add_source(self, kind: str, **kw) -> int:
        return self.store.add_source(kind, **kw)

    def add_section(self, source_id: int, text: str, heading_path: str = "", position: int = 0,
                    by: Optional[str] = None) -> int:
        return self.store.add_section(source_id, text, heading_path, position, by)

    def learn(self, text: str, origin: str, subject: str = "", category: str = "",
              section_id: Optional[int] = None, by: Optional[str] = None) -> int:
        """Record a statement. It starts as 'submitted' (never verified)."""
        return self.store.add_claim(text, origin, by, subject, category, section_id)

    def add_evidence(self, claim_id: int, kind: str, source_id: Optional[int] = None,
                     locator: str = "", by: Optional[str] = None) -> int:
        return self.store.add_evidence(claim_id, kind, source_id, locator, by)

    def link(self, from_claim: int, to_claim: int, kind: str, by: Optional[str] = None) -> None:
        self.store.link(from_claim, to_claim, kind, by)

    # -- reviewing / correcting -------------------------------------------------------
    def review(self, claim_id: int, status: str, reviewer: Optional[str] = None,
               note: str = "") -> None:
        self.store.review(claim_id, status, reviewer, note)

    def correct(self, claim_id: int, new_text: str, reason: str = "", by: Optional[str] = None) -> int:
        return self.store.correct(claim_id, new_text, by, reason)

    def propose_correction(self, claim_id: int, new_text: str, reason: str = "",
                           by: Optional[str] = None) -> int:
        return self.store.propose_correction(claim_id, new_text, by, reason)

    def propose_share(self, claim_id: int, by: Optional[str] = None) -> None:
        self.store.propose_share(claim_id, by)

    def approve_share(self, claim_id: int, moderator: str, note: str = "") -> None:
        self.store.approve_share(claim_id, moderator, note)

    # -- reading (always for a viewer) -------------------------------------------------
    def claim(self, claim_id: int, viewer: Optional[str] = None) -> Optional[dict]:
        return self.store.claim(claim_id, viewer)

    def explain(self, claim_id: int, viewer: Optional[str] = None) -> Dict[str, object]:
        """Everything behind one claim: evidence, its summary, links, history.
        Empty if the viewer may not see the claim."""
        c = self.store.claim(claim_id, viewer)
        if c is None:
            return {"claim": None, "evidence": [], "support": {}, "links": [],
                    "events": [], "versions": []}
        return {"claim": c,
                "evidence": self.store.evidence(claim_id),
                "support": self.store.evidence_summary(claim_id),
                "links": self.store.links(claim_id, viewer),
                **self.store.history(claim_id, viewer)}

    def search(self, query: str, limit: int = 10, modules: Optional[List[str]] = None,
               viewer: Optional[str] = None, include_rejected: bool = False,
               use_fts: Optional[bool] = None) -> List[Hit]:
        """Relevant knowledge for `query` (B2 retrieval, see retrieve.py):
        synonyms and word forms, rare words weigh more, subject and kind of
        question recognised, verified slightly preferred, conflicts shown
        together, duplicates merged. Only what `viewer` may see is used.
        Each Hit.detail["why"] explains its match. Same call as in B1."""
        from .retrieve import search as b2_search
        return b2_search(self.store, self.sources, query, viewer, limit, modules,
                         include_rejected, use_fts)

    def search_b1(self, query: str, limit: int = 10, modules: Optional[List[str]] = None,
                  viewer: Optional[str] = None) -> List[Hit]:
        """The B1 word-overlap search, kept for comparison / older callers."""
        hits: List[Hit] = []
        if modules is None or "library" in modules:
            for c in self.store.claims(limit=5000, viewer=viewer):
                score = overlap_score(query, f"{c['text']} {c['subject'] or ''} {c['category'] or ''}")
                if score > 0 and c["status"] != "rejected":
                    s = self.store.evidence_summary(c["id"])
                    hits.append(Hit("library", str(c["id"]), c["text"], c["status"], c["origin"],
                                    score, c["category"] or "",
                                    [f"{s['direct']} direct, {s['confirmations']} confirmations, "
                                     f"{s['assertions']} unsupported statements"],
                                    {"version": c["version"], "scope": c["scope"]}))
        for src in self.sources:
            if modules is None or src.module in modules:
                hits += src.search(query, limit)
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def ask(self, question: str, viewer: Optional[str] = None) -> BrainAnswer:
        """Answer from connected facts (B3, brain/reason.py), only from claims
        the viewer may see. Answered only when every claim used is verified;
        otherwise it says why it cannot establish the answer. Never invents."""
        from .reason import FactIndex, answer, plan, steps_of
        from .retrieve import visible_library_docs
        hits = self.search(question, viewer=viewer)
        p = plan(question)
        if p is None:
            return BrainAnswer(question, answered=False, basis="insufficient", confidence="none",
                               hits=hits, trace=[f"found {len(hits)} related records",
                                                 "not a question SK can reason about (yet): "
                                                 "no answer given"])
        r = answer(p, FactIndex(visible_library_docs(self.store, viewer)))
        steps = [{"ask": st.ask, "outcome": st.outcome, "value": st.display or st.value,
                  "verified": st.verified, "candidates": st.candidates,
                  "claims": [{"id": s.claim_id, "text": s.text, "status": s.status,
                              "scope": s.scope} for s in st.supports],
                  "also_unverified": [{"id": s.claim_id, "text": s.text, "status": s.status,
                                       "scope": s.scope} for s in st.also_unverified]}
                 for st in r.steps]
        answered = r.status in ("established", "absent")
        return BrainAnswer(question, answered=answered, answer=r.answer,
                           basis=("derived" if steps_of(p) > 1 else "documented") if answered
                           else "insufficient",
                           confidence="verified" if answered else
                           ("unverified" if r.status == "unverified" else "none"),
                           hits=hits, trace=r.explanation, status=r.status, possible=r.possible,
                           steps=steps, audience=r.audience if answered or r.possible else "")

    def respond(self, question: str, viewer: Optional[str] = None):
        """The answer in exactly one category (B4, brain/respond.py):
        established, documented, ai_generated (never before the AI gateway) or
        unknown with a reason. Built on ask() and search(), which are unchanged.
        Read-only: never writes, stores or changes anything."""
        from .respond import respond as b4_respond
        return b4_respond(self, question, viewer)

    def stats(self, viewer: Optional[str] = None) -> dict:
        return self.store.stats(viewer)
