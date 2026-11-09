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
    LEVELS = ["quote", "full_text"]                    # what Ana grants (details: title, date)

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
        self.shared = m.approve(self.fid("ana"), ana.propose(c, {sid: self.LEVELS}))
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


# ============================================================ F1 / F5-b: names in the step labels
class PlaceNamesInStepLabels(World):
    """Found by the security review: a step label ("who looks after it (<place>)") names the
    place as written in the FIRST claim at that place, in claim-id order - a personal claim of
    the asker (or a personal entity) created BEFORE the shared claim was approved named it, so
    a name the asker did not type reached a response marked shared. Now a shared claim names it."""
    SECRET = "PERSONAL MUMS GARDEN"                  # as the label writes it

    def setUp(self):
        super().setUp()
        self.ids["rita"] = self.lib.create_user("Rita")

    def personal_first(self):
        luis = self.open("luis")
        e = luis.add_entity("PERSONAL-MUMS-GARDEN", "room")
        luis.approve_alias(luis.add_alias(e, "Lantern Garden"))
        luis.learn("Odo runs PERSONAL-MUMS-GARDEN.", "user", "PERSONAL-MUMS-GARDEN")
        return luis

    def share_mira(self):
        ana = self.open("ana")
        sid = self.imp(ana, "Mira maintains the Lantern Garden.\n", "a.txt", publisher="Harbour Press")
        c = ana.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        ana.cite(c, sid, "Mira maintains the Lantern Garden.")
        ana.attest_source(sid, "checked by me")
        m = self.open("mod")
        shared = m.approve(self.fid("ana"), ana.propose(c, {sid: ["quote", "full_text"]}))
        ssid = m.store._db.execute("SELECT source_id FROM evidence WHERE claim_id=?",
                                   (shared,)).fetchone()[0]
        m.attest_source(ssid, "checked by a moderator")
        m.review(shared, "verified")

    def check(self, luis):
        r, b = luis.respond(Q_MG), luis.ask(Q_MG)
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        labels = [st["ask"] for st in r.steps]
        self.assertEqual(labels, ["who looks after it (Lantern Garden)"])      # the shared name
        for text in (blob(r, drop=()), " ".join(b.trace), json.dumps(b.steps)):
            self.assertNotIn(self.SECRET, text.upper().replace("-", " "))
        self.assertEqual(r.audience, "shared")

    def test_a_personal_claim_created_first_no_longer_names_the_place_in_a_shared_answer(self):
        luis = self.personal_first()           # lower claim ids than the shared claim
        self.share_mira()
        self.check(luis)

    def test_a_personal_claim_created_after_the_share_does_not_either(self):
        self.share_mira()
        luis = self.personal_first()
        self.check(luis)

    def test_nobody_else_ever_sees_the_personal_name(self):
        self.personal_first()
        self.share_mira()
        r = self.open("rita").respond(Q_MG)
        self.assertEqual([st["ask"] for st in r.steps], ["who looks after it (Lantern Garden)"])
        self.assertNotIn(self.SECRET, blob(r, drop=()).upper().replace("-", " "))


class MultiStepAnswers(World):
    """A personal unverified claim on a step that is NOT the last: the F1 rule must look at
    every step of a derived answer (the review noted no test had more than one step)."""
    GATE = "The north gate of the Plaza Nube leads to the Jardin Nube."
    Q = "Who maintains the room the north gate of the Plaza Nube leads to?"

    def setUp(self):
        super().setUp()
        self.ids["rita"] = self.lib.create_user("Rita")

    def share(self, text, subject):
        ana = self.open("ana")
        sid = self.imp(ana, text + "\n", "s%d.txt" % len(self.sessions), publisher="Harbour Press")
        c = ana.learn(text, "imported", subject)
        ana.cite(c, sid, text)
        ana.attest_source(sid, "checked by me")
        m = self.open("mod")
        shared = m.approve(self.fid("ana"), ana.propose(c, {sid: ["quote", "full_text"]}))
        ssid = m.store._db.execute("SELECT source_id FROM evidence WHERE claim_id=?",
                                   (shared,)).fetchone()[0]
        m.attest_source(ssid, "checked by a moderator")
        m.review(shared, "verified")

    def two_hops(self):
        self.share(self.GATE, "Plaza Nube")
        self.share("Mira maintains the Jardin Nube.", "Jardin Nube")

    def test_the_shared_chain_answers_for_everyone(self):
        self.two_hops()
        r = self.open("rita").respond(self.Q)
        self.assertEqual((r.category, r.value, r.audience), ("established", "Mira", "shared"))
        self.assertEqual(len(r.steps), 2)

    def test_a_personal_unverified_claim_on_the_first_step_makes_the_answer_personal(self):
        self.two_hops()
        luis = self.open("luis")
        luis.learn(self.GATE, "user", "Plaza Nube")        # personal, unverified: "also stated"
        r, b = luis.respond(self.Q), luis.ask(self.Q)
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        self.assertEqual([len(st["also_unverified"]) for st in r.steps], [1, 0])
        self.assertEqual(r.audience, "personal")
        self.assertEqual(b.audience, "personal")
        rita = self.open("rita").respond(self.Q)
        self.assertEqual((rita.audience, [len(st["also_unverified"]) for st in rita.steps]),
                         ("shared", [0, 0]))

    def test_a_personal_unverified_claim_on_the_last_step_makes_the_answer_personal(self):
        self.two_hops()
        luis = self.open("luis")
        luis.learn("The Jardin Nube is maintained by Mira.", "user", "Jardin Nube")
        r = luis.respond(self.Q)
        self.assertEqual([len(st["also_unverified"]) for st in r.steps], [0, 1])
        self.assertEqual(r.audience, "personal")


class ReverseQuestions(PlaceNamesInStepLabels):
    """Found by the second security review (reproduced): a REVERSE question ("which room does
    Mira look after?") names the answer's place from the canonical key, which an alias of the
    asker's own personal entity can turn into that entity's key - so the personal name became
    the ANSWER of a response marked shared. The place is now named as the supporting claim
    writes it."""
    QUESTIONS = ["Which room does Mira look after?", "Which place does Mira maintain?",
                 "What does Mira maintain?"]

    def test_the_answer_of_a_reverse_question_is_the_shared_name(self):
        luis = self.personal_first()
        self.share_mira()
        for q in self.QUESTIONS:
            r, b = luis.respond(q), luis.ask(q)
            self.assertEqual((r.category, r.value, r.audience), ("established", "Lantern Garden",
                                                                 "shared"), q)
            for text in (blob(r, drop=("related",)), json.dumps(b.steps), " ".join(b.trace),
                         str(b.answer)):
                self.assertNotIn(self.SECRET, text.upper().replace("-", " "), q)

    def test_the_same_with_the_personal_claim_created_after_the_share(self):
        self.share_mira()
        luis = self.personal_first()
        for q in self.QUESTIONS:
            r = luis.respond(q)
            self.assertEqual((r.value, r.audience), ("Lantern Garden", "shared"), q)

    def test_a_reverse_question_as_the_first_step_of_a_longer_question(self):
        luis = self.personal_first()
        self.share_mira()
        r, b = luis.respond("What is the room ID of the room Mira maintains?"), \
            luis.ask("What is the room ID of the room Mira maintains?")
        self.assertEqual([st["value"] for st in r.steps][:1], ["Lantern Garden"])
        self.assertNotIn(self.SECRET, (blob(r, drop=("related",)) + " ".join(b.trace)).upper()
                         .replace("-", " "))

    def test_nobody_else_gets_anything_of_it(self):
        self.personal_first()
        self.share_mira()
        for q in self.QUESTIONS:
            r = self.open("rita").respond(q)
            self.assertEqual((r.value, r.audience), ("Lantern Garden", "shared"), q)


class NothingOfTheAskersOwnRowsInASharedResponse(SharedVerifiedAnswer):
    """The generic guard the review asked for. It collects EVERY string in the asker's own
    personal rows (claim texts, entity and alias names, source titles, evidence quotes) and
    requires that none of them - normalised - appears in any response or answer marked shared.
    The setup makes sure shared answers are really reached (an alias of a personal entity
    canonicalises the shared place; no personal duplicate of the shared fact) and the test
    FAILS if no shared response was checked."""

    @staticmethod
    def norm(s):
        import re
        return re.sub(r"[^0-9a-z]+", " ", s.lower()).strip()

    def own_strings(self, session):
        import sqlite3
        con = session.store._db
        out = set()
        for sql in ("SELECT text FROM main.claims", "SELECT name FROM main.entities",
                    "SELECT alias FROM main.aliases", "SELECT title FROM main.sources",
                    "SELECT locator FROM main.evidence"):
            try:
                out |= {r[0] for r in con.execute(sql) if r[0]}
            except sqlite3.OperationalError:                  # a table this layout lacks
                pass
        return {self.norm(x) for x in out if len(self.norm(x)) >= 8}

    def test_no_string_of_the_askers_own_rows_is_in_a_shared_response(self):
        luis = self.open("luis")
        e = luis.add_entity("LUIS-SECRET-ROOM-NAME", "room")
        luis.approve_alias(luis.add_alias(e, "Lantern Garden"))     # canonicalises the SHARED place
        luis.approve_alias(luis.add_alias(e, "LUIS-SECRET-ALIAS-NAME"))
        sid = self.imp(luis, "LUIS-PRIVATE-QUOTE-SENTENCE belongs to nobody else.\n", "p.txt",
                       title="LUIS-SECRET-SOURCE-TITLE", publisher="My Notes")
        c = luis.learn("LUIS-PERSONAL-CLAIM about the garden keeper.", "user", "Lantern Garden")
        luis.cite(c, sid, "LUIS-PRIVATE-QUOTE-SENTENCE belongs to nobody else.")
        luis.learn("Odo runs LUIS-SECRET-ROOM-NAME.", "user", "LUIS-SECRET-ROOM-NAME")
        mine = self.own_strings(luis)
        mine.discard(self.norm("Lantern Garden"))     # his alias IS the shared place's own name
        self.assertTrue(any("luis secret room name" in x for x in mine))
        questions = [Q_MG, "Who maintains the LUIS-SECRET-ALIAS-NAME?",
                     "Which room does Mira look after?", "What does Mira maintain?",
                     "Who looks after the Lantern Garden?", "Who maintiains the Lantern Garden?"]
        shared_seen = 0
        for q in questions:
            r, b = luis.respond(q), luis.ask(q)
            for label, audience, text in (
                    ("respond", r.audience, blob(r, drop=("related", "question"))),
                    ("ask", b.audience, json.dumps(b.steps) + " ".join(b.trace) + str(b.answer))):
                if audience == "shared":
                    shared_seen += 1
                    for mark in mine:
                        self.assertNotIn(mark, self.norm(text), (q, label, mark))
        self.assertGreater(shared_seen, 3, "the guard did not reach shared responses")


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

    def test_a_typo_never_pulls_in_a_personal_alias_the_asker_did_not_type(self):
        """N1 (suspected by the review, not reproduced): near-miss personal names next to a
        shared name must not appear in understood_as / interpretation / clarification of a
        response marked shared."""
        luis = self.open("luis")
        e = luis.add_entity("Lantern Gardens Annex", "room")
        luis.approve_alias(luis.add_alias(e, "Lantern Gardens"))
        luis.learn("Odo runs the Lantern Gardens Annex.", "user", "Lantern Gardens Annex")
        for q in ("Who maintains the Lantern Gardn?", "Who maintains the Lantern Gardens?",
                  "Who is in charge of the Lantern Gardn?", "Who maintiains the Lantern Gardn?",
                  "who looks after the lantern garden"):
            r = luis.respond(q)
            if r.audience == "shared":
                text = r.understood_as + json.dumps(r.interpretation or {}) + \
                    json.dumps(r.clarification or {}, ensure_ascii=False)
                for word in ("Gardens", "Annex"):
                    if word in text:
                        self.assertIn(word, q, (q, "personal name shown but not typed"))

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
