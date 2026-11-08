"""Synthetic B5.2 world: five people and a moderator, privacy markers in every
kind of row, one proposal shared with full permission. Used by
tests/test_brain_b52.py and tests/b52_child.py (crash tests in a separate
process). Synthetic data only."""
from __future__ import annotations

import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402

# markers that must never leave their owner's own files (unless granted)
PERSONAL = {
    "ana": ["ANA-PRIVATE-NOTES-MARK", "Secret Cellar", "Zeta", "ANA-ENTITY-MARK", "ANA-ALIAS-MARK",
            "ANA-OLD-WORDING-MARK", "ANA-REVIEW-NOTE-MARK", "ANA-DISPLAY-MARK",
            "ANA-UNSHARED-TITLE-MARK", "plainhash-ana-unshared"],
    "luis": ["LUIS-PRIVATE-NOTES-MARK", "Hidden Vault", "Kai", "LUIS-ENTITY-MARK",
             "LUIS-ALIAS-MARK", "LUIS-DISPLAY-MARK", "plainhash-luis-0001"],
    "eve": ["EVE-CLAIM-MARK", "EVE-DISPLAY-MARK"],
    "kim": ["KIM-CLAIM-MARK", "KIM-DISPLAY-MARK"],
}
# Ana's granted material (quote, details, full text): in shared.db until withdrawn
GRANTED = ["ANA-TITLE-MARK", "ANA-REF-MARK", "ANA-FULLTEXT-MARK", "2026-01-02"]
GRANTED_QUOTE = "Mira maintains the Lantern Garden."
PLAIN_HASH = "plainhash-ana-0001"


class World:
    def __init__(self, root: str, hk: bytes = None, lk: bytes = None):
        self.root = root
        self.hk = hk or secrets.token_bytes(32)
        self.lk = lk or secrets.token_bytes(32)
        self.lib = Library(os.path.join(root, "brain"), self.hk, self.lk)

    def grant(self, uid, role="user"):
        return mint_grant(self.hk, self.lib.installation_id, self.lib.epoch, uid, role)

    def open(self, who, role="user"):
        return self.lib.open_session(self.grant(self.ids[who], role))

    def build(self):
        lib = self.lib
        self.ids = {"ana": lib.create_user("Ana ANA-DISPLAY-MARK"),
                    "luis": lib.create_user("Luis LUIS-DISPLAY-MARK", "external_user"),
                    "eve": lib.create_user("Eve EVE-DISPLAY-MARK", "external_user"),
                    "kim": lib.create_user("Kim KIM-DISPLAY-MARK", "external_user"),
                    "mod": lib.create_user("Moderator", "moderator")}
        s = self.open("ana")
        self.ana_src = s.add_source("document", title="ANA-TITLE-MARK", reference="ANA-REF-MARK",
                                    raw_text="ANA-FULLTEXT-MARK", content_hash=PLAIN_HASH,
                                    captured_at="2026-01-02")
        self.ana_claim = s.learn(GRANTED_QUOTE, "imported", "Lantern Garden")
        s.add_evidence(self.ana_claim, "direct", self.ana_src, GRANTED_QUOTE)
        s.review(self.ana_claim, "verified", note="ANA-REVIEW-NOTE-MARK")
        priv = s.add_source("document", title="ANA-PRIVATE-NOTES-MARK", private=True)
        c = s.learn("Zeta maintains the Secret Cellar.", "user", "Secret Cellar")
        s.add_evidence(c, "direct", priv, "Zeta maintains the Secret Cellar.")
        s.review(c, "verified")
        other = s.add_source("document", title="ANA-UNSHARED-TITLE-MARK",
                             content_hash="plainhash-ana-unshared")
        c2 = s.learn("The Old Pier has room ID 1.", "imported", "Old Pier")
        s.add_evidence(c2, "direct", other, "The Old Pier has room ID 1.")
        c3 = s.learn("ANA-OLD-WORDING-MARK about the Old Pier.", "user", "Old Pier")
        s.correct(c3, "The Old Pier opens early.", "fixed")
        e = s.add_entity("ANA-ENTITY-MARK", "room")
        s.add_alias(e, "ANA-ALIAS-MARK")
        self.ana_token = s.propose(self.ana_claim, {self.ana_src: ["quote", "details",
                                                                    "full_text"]})
        s.close()
        s = self.open("luis")
        src = s.add_source("document", title="LUIS-PRIVATE-NOTES-MARK", private=True)
        c = s.learn("Kai maintains the Hidden Vault.", "user", "Hidden Vault")
        s.add_evidence(c, "direct", src, "Kai maintains the Hidden Vault.")
        s.review(c, "verified")
        pub = s.add_source("document", title="Luis guide", content_hash="plainhash-luis-0001")
        c = s.learn("The Hidden Vault has room ID 999111.", "imported", "Hidden Vault")
        s.add_evidence(c, "direct", pub, "The Hidden Vault has room ID 999111.")
        e = s.add_entity("LUIS-ENTITY-MARK", "room")
        s.add_alias(e, "LUIS-ALIAS-MARK")
        s.close()
        for who, text in (("eve", "EVE-CLAIM-MARK is a note."), ("kim", "KIM-CLAIM-MARK note.")):
            s = self.open(who)
            s.learn(text, "user")
            s.close()
        m = self.open("mod", "moderator")
        p = [x for x in m.proposals() if x["token"] == self.ana_token][0]
        self.shared_claim = m.approve(p["file"], self.ana_token)
        m.review(self.shared_claim, "verified")
        m.close()
        return self

    # -- where things are ---------------------------------------------------------------
    def files(self):
        """Every file of the library (databases, sidecars, everything)."""
        out = []
        for d, _, names in os.walk(os.path.join(self.root, "brain")):
            for n in names:
                out.append(os.path.join(d, n))
        return out

    def owner_files(self, who):
        fid = self.lib._user(self.ids[who])["file_id"]
        return {p for p in self.files() if os.path.basename(p).startswith(fid)}

    def personal_files(self, who):
        fid = self.lib._user(self.ids[who])["file_id"]
        return {p for p in self.files() if os.path.basename(p).startswith(fid) and
                os.sep + "personal" + os.sep in p}

    def where(self, marker: str):
        b = marker.encode("utf-8")
        hits = set()
        for p in self.files():
            try:
                with open(p, "rb") as fh:
                    if b in fh.read():
                        hits.add(p)
            except OSError:
                pass
        return hits
