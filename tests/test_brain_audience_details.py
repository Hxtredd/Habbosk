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
    TITLE_ONLY = "ANA-TITLE-ONLY-MARK"
    QUOTE = "ANA-PRIVATE-QUOTE-MARK says: Pia runs the Net Loft."

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
        # a source with a title but NO date
        s3 = b.add_source("document", title=self.TITLE_ONLY, by=ana)
        c3 = b.learn("Kai runs the Fish Market.", "imported", "Fish Market", by=ana)
        b.add_evidence(c3, "direct", s3, "Kai runs the Fish Market.", by=ana)
        b.propose_share(c3, by=ana)
        b.review(c3, "verified", reviewer=mod)
        b.approve_share(c3, mod)
        # a source with NO title and NO date - only a reference - whose QUOTE differs from the
        # claim's text: the quote is the personal detail (the auditor's F2-a case)
        s4 = b.add_source("document", title="", reference="ANA-ONLY-REF", by=ana)
        c4 = b.learn("Pia runs the Net Loft.", "imported", "Net Loft", by=ana)
        b.add_evidence(c4, "direct", s4, self.QUOTE, by=ana)
        b.propose_share(c4, by=ana)
        b.review(c4, "verified", reviewer=mod)
        b.approve_share(c4, mod)
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

    def test_a_title_alone_is_a_personal_detail_too(self):
        r = self.session("local_user").respond("Who runs the Fish Market?")
        self.assertEqual(r.value, "Kai")
        e = self.evidence(r)[0]
        self.assertEqual((e["source"], e["date"]), (self.TITLE_ONLY, ""))
        self.assertEqual(r.audience, "personal")
        o = self.session("external_user").respond("Who runs the Fish Market?")
        self.assertEqual(o.audience, "shared")
        self.assertNotIn(self.TITLE_ONLY, blob(o))

    def test_a_quote_alone_is_a_personal_detail_even_without_title_or_date(self):
        """No title, no date, only a quote the owner never granted: the union view still hands
        the owner HER quote under the shared evidence id, so her response must be personal;
        the other person gets the shared view with the quote withheld."""
        r = self.session("local_user").respond("Who runs the Net Loft?")
        self.assertEqual((r.category, r.value), ("established", "Pia"))
        e = self.evidence(r)[0]
        self.assertEqual((e["source"], e["date"]), ("", ""))
        self.assertIn("ANA-PRIVATE-QUOTE-MARK", e["quote"])          # the owner still sees it
        self.assertEqual(r.audience, "personal")
        o = self.session("external_user").respond("Who runs the Net Loft?")
        self.assertEqual((o.category, o.audience), ("established", "shared"))
        self.assertEqual(self.evidence(o)[0]["quote"], "")
        self.assertNotIn("ANA-PRIVATE-QUOTE-MARK", blob(o))
        self.assertNotIn("ANA-ONLY-REF", blob(o))

    def test_a_demoted_claim_with_only_a_quote_is_not_marked_shared_for_the_owner(self):
        import sqlite3
        self.lib.close()
        con = sqlite3.connect(os.path.join(self.d, "brain", "shared.db"))
        con.execute("UPDATE claims SET status='submitted'")
        con.commit()
        con.close()
        self.lib = Library(os.path.join(self.d, "brain"), self.hk, self.lk)
        self.addCleanup(self.lib.close)
        r = self.session("local_user").respond("Who runs the Net Loft?")
        self.assertNotEqual(r.audience, "shared")              # documented or not: it is hers
        o = self.session("external_user").respond("Who runs the Net Loft?")
        self.assertNotIn("ANA-PRIVATE-QUOTE-MARK", blob(o))

    def test_a_demoted_shared_claim_answered_as_documented_names_the_title_only_to_the_owner(self):
        """The Documented branch: demote the shared claim in this temporary library (no longer
        verified) and the owner's answer reads 'According to <her title>, <her date>' - in the
        SENTENCE itself. It must be personal; the other person gets no title at all."""
        import sqlite3
        self.lib.close()
        con = sqlite3.connect(os.path.join(self.d, "brain", "shared.db"))
        con.execute("UPDATE claims SET status='submitted'")
        con.commit()
        con.close()
        self.lib = Library(os.path.join(self.d, "brain"), self.hk, self.lk)
        self.addCleanup(self.lib.close)
        r = self.session("local_user").respond("Who maintains the Lantern Garden?")
        self.assertEqual(r.category, "documented")
        self.assertIn(self.TITLE, r.sentence)                  # the owner's own title is shown
        self.assertEqual(r.audience, "personal")
        o = self.session("external_user").respond("Who maintains the Lantern Garden?")
        for mark in (self.TITLE, "ANA-DIARY-REF", self.DATE):
            self.assertNotIn(mark, blob(o))

    def test_no_response_marked_shared_holds_a_personal_title_or_date(self):
        for kind in ("local_user", "external_user"):
            s = self.session(kind)
            for q in ("Who maintains the Lantern Garden?", "Who runs the Rope Shed?",
                      "Who runs the Fish Market?", "Who runs the Net Loft?"):
                r = s.respond(q)
                if r.audience == "shared":
                    for mark in (self.TITLE, self.DATE, self.DATE_ONLY, self.TITLE_ONLY,
                                 "ANA-PRIVATE-QUOTE-MARK", "ANA-ONLY-REF"):
                        self.assertNotIn(mark, blob(r), (kind, q))


class GrantedDetailsStayShared(SharedVerifiedAnswer):
    """Ana grants the DETAILS level: the shared copy has its own title (shared on purpose). The
    other viewer sees that title and the response stays shared - the F2 rule is only for
    details that come from the viewer's own personal row."""
    LEVELS = ["quote", "details", "full_text"]

    def test_a_title_that_was_granted_is_shared_and_keeps_the_response_shared(self):
        r = self.open("rita").respond(Q_MG)
        e = [x for c in r.citations for x in c.evidence][0]
        self.assertEqual((r.category, r.audience, e["withheld"]), ("established", "shared", False))
        self.assertEqual(e["source"], "a.txt")                  # the shared copy's own title
        ana = self.open("ana")
        self.assertEqual([x.get("personal_source") for x in ana.store.cited_evidence(self.shared)],
                         [False])                               # her own copy is a different source


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

    def test_the_flag_is_only_ever_set_for_shared_claims(self):
        ana = self.open("ana")
        self.assertEqual([x["personal_source"] for x in ana.store.cited_evidence(self.ana_claim)],
                         [False])                                # her personal claim: not "a shared claim"

    def test_the_owner_of_a_shared_claim_gets_a_personal_answer_because_of_her_own_copy(self):
        ana = self.open("ana")
        r = ana.respond(Q_MG)
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        also = r.steps[0]["also_unverified"]
        self.assertEqual([(c["id"], c["scope"]) for c in also], [(self.ana_claim, "personal")])
        self.assertEqual(r.audience, "personal")           # her own copy rides along: conservative


if __name__ == "__main__":
    unittest.main()
