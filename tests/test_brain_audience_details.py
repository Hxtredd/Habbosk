"""Audience privacy, finding F2 of the B6.1 security review: a library MIGRATED from the
legacy single file (split.py) keeps the owner's FULL row of a source under the same id as the
shared stub (whose title and date were never granted), and the union view lets the owner's
row win. The owner's response to a shared claim showed her own source title and date while
marked audience="shared"; if passed on, those details went with it. Now such a response is
audience="personal" (the owner still sees everything); another viewer gets the shared view
with the details withheld; a normal B6 share (new source ids) is not affected.
"""
import os
import secrets
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from puzzle_assistant.brain import split as S  # noqa: E402
from puzzle_assistant.brain.api import Brain  # noqa: E402
from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402
from tests.test_brain_audience_privacy import Q_MG, SharedVerifiedAnswer, blob  # noqa: E402


class MigratedOwnerSourceDetails(unittest.TestCase):
    """A library migrated from the legacy single file (split.py): the owner's own full row of
    a source stays under the id the shared stub uses."""
    TITLE = "ANA-FIELD-DIARY-TITLE"
    DATE = "2026-02-03"
    DATE_ONLY = "2026-03-04"

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_f2_")
        self.addCleanup(shutil.rmtree, self.d, True)
        b = Brain(os.path.join(self.d, "knowledge.db"), "", "")
        ana = b.local_user
        luis = b.store.add_contributor("Luis (legacy)", "external_user")
        mod = b.store.add_contributor("Moderator (legacy)", "moderator")
        self.luis_c = luis
        s1 = b.add_source("document", title=self.TITLE, reference="ANA-DIARY-REF",
                          captured_at=self.DATE, by=ana)
        c1 = b.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden", by=ana)
        b.add_evidence(c1, "direct", s1, "Mira maintains the Lantern Garden.", by=ana)
        b.propose_share(c1, by=ana)
        b.review(c1, "verified", reviewer=mod)
        b.approve_share(c1, mod)
        # a source with NO title, only a date: the date alone is a personal detail too
        s2 = b.add_source("document", title="", captured_at=self.DATE_ONLY, by=ana)
        c2 = b.learn("Odo runs the Rope Shed.", "imported", "Rope Shed", by=ana)
        b.add_evidence(c2, "direct", s2, "Odo runs the Rope Shed.", by=ana)
        b.propose_share(c2, by=ana)
        b.review(c2, "verified", reviewer=mod)
        b.approve_share(c2, mod)
        b.close()
        self.hk, self.lk = secrets.token_bytes(32), secrets.token_bytes(32)
        S.split_file(os.path.join(self.d, "knowledge.db"), self.d, self.hk, self.lk)
        self.lib = Library(os.path.join(self.d, "brain"), self.hk, self.lk)
        self.addCleanup(self.lib.close)
        self.sessions = []

    def session(self, kind):
        u = [x for x in self.lib.users() if x["kind"] == kind][0]
        s = self.lib.open_session(mint_grant(self.hk, self.lib.installation_id, self.lib.epoch,
                                             u.get("user_id") or u["id"], "user"))
        self.sessions.append(s)
        self.addCleanup(s.close)
        return s

    def evidence(self, r):
        return [e for c in r.citations for e in c.evidence]

    def test_the_owner_sees_their_details_and_the_response_is_personal(self):
        r = self.session("local_user").respond("Who maintains the Lantern Garden?")
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        e = self.evidence(r)[0]
        self.assertEqual((e["source"], e["date"], e["withheld"]), (self.TITLE, self.DATE, False))
        self.assertEqual(r.audience, "personal")           # carries personal details

    def test_another_person_gets_the_shared_view_with_the_details_withheld(self):
        r = self.session("external_user").respond("Who maintains the Lantern Garden?")
        self.assertEqual((r.category, r.audience), ("established", "shared"))
        e = self.evidence(r)[0]
        self.assertEqual((e["source"], e["date"], e["withheld"]), ("", "", True))
        for mark in (self.TITLE, "ANA-DIARY-REF", self.DATE):
            self.assertNotIn(mark, blob(r))

    def test_a_date_alone_is_a_personal_detail_too(self):
        r = self.session("local_user").respond("Who runs the Rope Shed?")
        self.assertEqual(r.value, "Odo")
        self.assertEqual(self.evidence(r)[0]["date"], self.DATE_ONLY)
        self.assertEqual(r.audience, "personal")
        o = self.session("external_user").respond("Who runs the Rope Shed?")
        self.assertEqual(o.audience, "shared")
        self.assertNotIn(self.DATE_ONLY, blob(o))

    def test_no_response_marked_shared_holds_a_personal_title_or_date(self):
        for kind in ("local_user", "external_user"):
            s = self.session(kind)
            for q in ("Who maintains the Lantern Garden?", "Who runs the Rope Shed?"):
                r = s.respond(q)
                if r.audience == "shared":
                    for mark in (self.TITLE, self.DATE, self.DATE_ONLY):
                        self.assertNotIn(mark, blob(r), (kind, q))


class OrdinaryShareDoesNotTriggerTheSourceRule(SharedVerifiedAnswer):
    """B6 shares copy a source under NEW ids, so the owner's own copy is a different source:
    the F2 rule must not fire for them. (The owner's answer is still personal - but for the
    F1 reason: her own personal copy of the claim is listed 'also stated'. Both are tested.)"""

    def test_no_source_detail_is_flagged_personal_for_a_normal_share(self):
        ana = self.open("ana")
        self.assertEqual([e.get("personal_source") for e in ana.store.cited_evidence(self.shared)],
                         [False])
        r = self.open("rita").respond(Q_MG)
        self.assertEqual((r.category, r.audience), ("established", "shared"))

    def test_the_owner_of_a_shared_claim_gets_a_personal_answer_because_of_her_own_copy(self):
        ana = self.open("ana")
        r = ana.respond(Q_MG)
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        also = r.steps[0]["also_unverified"]
        self.assertEqual([(c["id"], c["scope"]) for c in also], [(self.ana_claim, "personal")])
        self.assertEqual(r.audience, "personal")           # her own copy rides along: conservative


if __name__ == "__main__":
    unittest.main()
