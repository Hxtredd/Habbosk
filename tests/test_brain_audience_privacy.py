"""Audience privacy (findings F1, F2, F5 of the B6.1 security review).

A response marked audience="shared" is meant to be safe to pass on to other people. Text or
details from the ASKER'S OWN personal material must never travel in one:

F1  an Established answer also lists, per step, the unverified claims "also stated" (their
    text is in `steps[].also_unverified`, and in ask()'s steps). The audience used to be
    computed from the supporting claims only, so the asker's own personal unverified claim
    rode along in a response marked shared. It now counts every claim whose text is carried.
F2  (tests/test_brain_audience_details.py)
F5  `understood_as` / `interpretation`: investigated - they only echo the names the asker
    typed ("as written"), never a personal name the asker did not type. Pinned here.

In every case the asker keeps every piece of information (only the label changes), the
category and value are unchanged, and another viewer gets the shared view.
"""
import dataclasses
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from tests.test_brain_b6 import Q_MG, World  # noqa: E402

PASSIVE = "The Lantern Garden is maintained by Mira."          # the same fact, personal wording


def blob(response, drop=("related",)):
    """Everything in a response except the fields that are by contract the asker's own view."""
    d = dataclasses.asdict(response)
    for k in drop:
        d.pop(k, None)
    return json.dumps(d, ensure_ascii=False)


def scopes_in_steps(steps):
    """(scope, text) of every claim whose text the steps carry."""
    out = []
    for st in steps:
        for key in ("claims", "also_unverified"):
            out += [(c["scope"], c["text"]) for c in st.get(key, [])]
    return out


class SharedVerifiedAnswer(World):
    """Ana's claim, shared by a moderator and verified: Established for everybody."""

    def setUp(self):
        super().setUp()
        self.ids["rita"] = self.lib.create_user("Rita")             # no material of their own
        ana = self.open("ana")
        sid = self.imp(ana, "Mira maintains the Lantern Garden.\n", "a.txt",
                       publisher="Harbour Press")
        c = ana.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        ana.cite(c, sid, "Mira maintains the Lantern Garden.")
        ana.attest_source(sid, "checked by me")
        self.ana_claim = c
        self.ana_sid = sid
        m = self.open("mod")
        self.shared = m.approve(self.fid("ana"), ana.propose(c, {sid: ["quote", "full_text"]}))
        ssid = m.store._db.execute("SELECT source_id FROM evidence WHERE claim_id=?",
                                   (self.shared,)).fetchone()[0]
        m.attest_source(ssid, "publisher page checked by a moderator")
        m.review(self.shared, "verified")


# ==================================================================================== F1
class UnverifiedClaimsInAnswers(SharedVerifiedAnswer):
    def personal_claim(self, who="luis", text=PASSIVE):
        s = self.open(who)
        s.learn(text, "user", "Lantern Garden")
        return s

    def test_a_personal_unverified_claim_makes_a_verified_answer_personal(self):
        luis = self.personal_claim()
        a = luis.respond(Q_MG)
        self.assertEqual((a.category, a.value), ("established", "Mira"))      # unchanged
        also = [c["text"] for c in a.steps[0]["also_unverified"]]
        self.assertIn(PASSIVE, also)                       # the asker still sees their own claim
        self.assertEqual(a.audience, "personal")           # ... and the response is theirs only

    def test_ask_reports_the_same_audience(self):
        luis = self.personal_claim()
        b = luis.ask(Q_MG)
        self.assertEqual(b.status, "established")
        self.assertIn(PASSIVE, [c["text"] for st in b.steps for c in st["also_unverified"]])
        self.assertIn(PASSIVE, " ".join(b.trace))          # the explanation carries it too
        self.assertEqual(b.audience, "personal")

    def test_another_viewer_gets_the_shared_view_without_it(self):
        self.personal_claim("luis")
        for who in ("rita", "ana"):
            r = self.open(who).respond(Q_MG)
            self.assertNotIn(PASSIVE, blob(r), who)
            self.assertNotIn(PASSIVE, " ".join(self.open(who).ask(Q_MG).trace), who)
        r = self.open("rita").respond(Q_MG)
        self.assertEqual((r.category, r.audience), ("established", "shared"))
        self.assertEqual(r.steps[0]["also_unverified"], [])

    def test_shared_unverified_claims_keep_the_answer_shared(self):
        """An unverified claim that is itself SHARED is shown 'also stated' and is nothing
        personal: the answer stays shared."""
        ana = self.open("ana")
        c2 = ana.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        ana.cite(c2, self.ana_sid, "Mira maintains the Lantern Garden.")
        ana.propose(c2, {self.ana_sid: ["quote", "full_text"]})
        m = self.open("mod")
        pending = [p for p in m.proposals() if p["file"] == self.fid("ana")]
        shared2 = m.approve(self.fid("ana"), pending[-1]["token"])           # approved, NOT verified
        self.assertNotEqual(shared2, self.shared)
        r = self.open("rita").respond(Q_MG)
        self.assertEqual((r.category, r.audience), ("established", "shared"))
        also = r.steps[0]["also_unverified"]
        self.assertTrue(also and all(c["scope"] == "shared" for c in also))

    def test_the_asker_loses_nothing_and_nothing_else_changes(self):
        base = self.open("rita").respond(Q_MG)
        luis = self.personal_claim()
        a = luis.respond(Q_MG)
        self.assertEqual((a.category, a.value, a.sentence), (base.category, base.value, base.sentence))
        self.assertEqual([c.claim_id for c in a.citations], [c.claim_id for c in base.citations])
        self.assertEqual(a.steps[0]["claims"], base.steps[0]["claims"])
        self.assertEqual(len(a.steps[0]["also_unverified"]), 1)

    def test_no_shared_response_or_answer_carries_a_personal_claim(self):
        """The guard: for every viewer and both entry points, anything marked shared holds
        only shared claims (supporting AND 'also stated'), with and without personal claims."""
        for with_personal in (False, True):
            if with_personal:
                self.personal_claim("luis")
                self.personal_claim("ana", "The Lantern Garden is looked after by Mira.")
            for who in ("ana", "luis", "rita"):
                s = self.open(who)
                r, b = s.respond(Q_MG), s.ask(Q_MG)
                for label, audience, steps in (("respond", r.audience, r.steps),
                                               ("ask", b.audience, b.steps)):
                    if audience == "shared":
                        self.assertTrue(all(sc == "shared" for sc, _ in scopes_in_steps(steps)),
                                        (who, label, with_personal, scopes_in_steps(steps)))

    def test_a_personal_claim_on_an_answer_that_is_not_established_stays_personal(self):
        """No verified claim at all: the answer rests on personal claims and is personal."""
        luis = self.open("luis")
        luis.learn("Odo runs the Rope Shed.", "user", "Rope Shed")
        a = luis.respond("Who runs the Rope Shed?")
        self.assertEqual(a.audience in ("", "personal"), True)
        self.assertNotEqual(a.audience, "shared")


# ==================================================================================== F5
class UnderstoodAsEchoesOnlyTheAskersWords(SharedVerifiedAnswer):
    """Investigated, not reproducible: understood_as shows names "as written" in the question.
    These tests pin that, so a change that shows a personal name the asker did not type fails."""
    PARAPHRASES = ("Who is in charge of the {n}?", "Who takes care of the {n}?",
                   "Who maintiains the {n}?", "Who maintains the {n} garden?")

    def blob_all(self, r):
        return blob(r, drop=())            # understood_as / interpretation are NOT excluded

    def run_all(self, luis, names):
        seen = []
        for n in names:
            for tpl in self.PARAPHRASES:
                q = tpl.format(n=n)
                seen.append((q, luis.respond(q)))
        return seen

    def test_a_personal_alias_appears_in_understood_as_only_when_typed(self):
        luis = self.open("luis")
        e = luis.add_entity("Lantern Garden", "room")
        luis.approve_alias(luis.add_alias(e, "SECRET-ALIAS-LUIS"))
        for q, r in self.run_all(luis, ["Lantern Garden", "SECRET-ALIAS-LUIS"]):
            text = r.understood_as + json.dumps(r.interpretation or {}, ensure_ascii=False)
            if "SECRET-ALIAS-LUIS" in text:
                self.assertIn("SECRET-ALIAS-LUIS", q, q)       # only the asker's own words

    def test_a_personal_canonical_name_is_never_shown_to_someone_who_did_not_type_it(self):
        luis = self.open("luis")
        e = luis.add_entity("SECRET-CANON-LUIS", "room")
        luis.approve_alias(luis.add_alias(e, "Lantern Garden"))
        for q, r in self.run_all(luis, ["Lantern Garden", "Lantern Gardn"]):
            self.assertNotIn("SECRET-CANON-LUIS", self.blob_all(r), q)
        # and the others never see anything of Luis's names
        rita = self.open("rita")
        for q, r in self.run_all(rita, ["Lantern Garden"]):
            for mark in ("SECRET-CANON-LUIS", "SECRET-ALIAS-LUIS"):
                self.assertNotIn(mark, self.blob_all(r), q)

    def test_every_response_marked_shared_has_an_understood_as_with_only_typed_words(self):
        luis = self.open("luis")
        e = luis.add_entity("SECRET-CANON-LUIS", "room")
        luis.approve_alias(luis.add_alias(e, "Lantern Garden"))
        e2 = luis.add_entity("Lantern Garden", "room")
        luis.approve_alias(luis.add_alias(e2, "SECRET-ALIAS-LUIS"))
        for q, r in self.run_all(luis, ["Lantern Garden", "SECRET-ALIAS-LUIS"]):
            if r.audience == "shared":
                for mark in ("SECRET-CANON-LUIS", "SECRET-ALIAS-LUIS"):
                    if mark in self.blob_all(r):
                        self.assertIn(mark, q, (q, "personal name shown but not typed"))


if __name__ == "__main__":
    unittest.main()
