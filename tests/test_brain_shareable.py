"""B6.2: Response.shareable() / BrainAnswer.shareable() - a sanitized COPY that may be passed
on. Contaminated responses (personal names, source titles, passage text, typo corrections,
nested step content) must come out clean or be refused; the original is never changed.
There is no sharing feature: these tests only prepare and inspect copies."""
import copy
import dataclasses
import json
import unittest

from test_brain_b6 import World, Q_MG
from puzzle_assistant.brain.model import BrainAnswer, Citation, Hit, Response
from puzzle_assistant.brain.shareable import NotShareable

MARKS = ("LUIS-SECRET-ROOM", "KAI-PERSONAL-PASSAGE", "LUIS-SOURCE-TITLE", "MY-NOTES-PUBLISHER",
         "SECRET-VISIT", "LUIS-TYPED-QUESTION")


def dump(x):
    return json.dumps(dataclasses.asdict(x), ensure_ascii=False, default=str)


def contaminated(**over) -> Response:
    """A response marked shared whose every text-bearing field is contaminated."""
    ev_ok = {"kind": "direct", "quote": "Mira maintains the Lantern Garden.", "source": "Harbour",
             "source_kind": "document", "date": "2020", "page": None, "withheld": False,
             "personal_details": False}
    ev_bad = dict(ev_ok, quote="KAI-PERSONAL-PASSAGE quote", source="LUIS-SOURCE-TITLE",
                  personal_details=True)
    ev_unknown = {k: v for k, v in dict(ev_ok, source="LUIS-SOURCE-TITLE").items()
                  if k != "personal_details"}
    r = Response(
        question="Who maintains the LUIS-TYPED-QUESTION Lantern Garden?", language="en",
        category="established", value="Mira", sentence="Mira maintains the Lantern Garden.",
        citations=[Citation(1, "Mira maintains the Lantern Garden.", "verified", "shared",
                            [ev_ok, ev_bad, ev_unknown], True)],
        steps=[{"ask": "who maintains the Lantern Garden", "outcome": "value", "value": "Mira",
                "verified": True, "candidates": ["LUIS-SECRET-ROOM"],
                "claims": [{"id": 1, "text": "Mira maintains the Lantern Garden.",
                            "status": "verified", "scope": "shared",
                            "note": "LUIS-SECRET-ROOM"}],
                "also_unverified": [], "extra": {"deep": ["LUIS-SECRET-ROOM", {"x": "KAI-PERSONAL-PASSAGE"}]},
                "alias_note": "MY-NOTES-PUBLISHER"}],
        related=[{"text": "Luis visits SECRET-VISIT", "label": "x", "status": "unverified",
                  "module": "library"}],
        audience="shared", understood_as="read 'Lantern Gardn' as 'LUIS-SECRET-ROOM'",
        interpretation={"relation": "caretaker", "entity": "LUIS-SECRET-ROOM",
                        "corrections": [["Lantern Gardn", "LUIS-SECRET-ROOM"]]},
        challenges=[
            {"values": ["Kai"], "passages": 1, "sources": 1, "examples": [
                {"text": "KAI-PERSONAL-PASSAGE", "source": "LUIS-SOURCE-TITLE", "page": 2}],
             "publisher": "MY-NOTES-PUBLISHER", "labels": ["publisher MY-NOTES-PUBLISHER"],
             "scope": "personal", "internal": False, "counts_as": "a challenge only"},
            {"values": ["Odo"], "passages": 2, "sources": 1, "examples": [
                {"text": "Odo maintains the Lantern Garden.", "source": "LUIS-SOURCE-TITLE", "page": None}],
             "publisher": "MY-NOTES-PUBLISHER", "labels": ["author Luis, publisher MY-NOTES-PUBLISHER"],
             "scope": "shared", "internal": False, "counts_as": "a challenge only"}],
        sides=[{"value": "Mira", "from": "claims"},
               {"value": "Kai", "from": "passage", "group": "MY-NOTES-PUBLISHER", "scope": "personal"},
               {"value": "Odo", "from": "passage", "group": "MY-NOTES-PUBLISHER", "scope": "shared"}])
    return dataclasses.replace(r, **over) if over else r


class Contaminated(unittest.TestCase):
    def test_nothing_personal_survives(self):
        r = contaminated()
        s = r.shareable()
        for m in MARKS:
            self.assertNotIn(m, dump(s), m)

    def test_what_is_removed(self):
        s = contaminated().shareable()
        self.assertEqual((s.question, s.related, s.understood_as, s.interpretation,
                          s.clarification), ("", [], "", None, None))
        self.assertEqual([g["values"] for g in s.challenges], [["Odo"]])   # non-shared group gone
        self.assertEqual([x["from"] for x in s.sides], ["claims", "passage"])
        self.assertEqual(s.steps[0]["candidates"], [])
        self.assertEqual(set(s.steps[0]), {"ask", "outcome", "value", "verified", "candidates",
                                           "claims", "also_unverified"})
        self.assertEqual(set(s.steps[0]["claims"][0]), {"id", "text", "status", "scope"})
        self.assertEqual(set(s.challenges[0]["examples"][0]), {"text", "page"})
        self.assertNotIn("publisher", s.challenges[0])
        self.assertNotIn("labels", s.challenges[0])

    def test_what_is_kept(self):
        r = contaminated()
        s = r.shareable()
        for f in ("language", "category", "reason", "value", "sentence", "audience", "possible",
                  "ai", "b3_status"):
            self.assertEqual(getattr(s, f), getattr(r, f), f)
        self.assertEqual(s.steps[0]["value"], "Mira")
        self.assertEqual(s.steps[0]["claims"][0]["text"], "Mira maintains the Lantern Garden.")
        # citation: only the evidence explicitly established as not personal is kept
        self.assertEqual(len(s.citations), 1)
        self.assertEqual([e["source"] for e in s.citations[0].evidence], ["Harbour"])
        self.assertEqual(s.challenges[0]["examples"][0]["text"], "Odo maintains the Lantern Garden.")

    def test_the_original_is_unchanged_and_not_shared_with_the_copy(self):
        r = contaminated()
        before, snap = dump(r), copy.deepcopy(r)
        s = r.shareable()
        self.assertEqual(dump(r), before)
        self.assertEqual(r, snap)
        self.assertIsNot(s, r)
        # mutating the copy at every nesting level cannot reach the original
        s.steps[0]["claims"][0]["text"] = "CHANGED"
        s.steps.append({"x": 1})
        s.citations[0].evidence[0]["quote"] = "CHANGED"
        s.challenges[0]["examples"][0]["text"] = "CHANGED"
        s.sides.clear()
        self.assertEqual(r, snap)
        self.assertEqual(dump(r), before)
        # the personal view still has everything
        self.assertIn("LUIS-TYPED-QUESTION", r.question)
        self.assertEqual(len(r.related), 1)
        self.assertEqual(len(r.challenges), 2)

    def test_a_clean_response_passes_unchanged_apart_from_the_asker_only_fields(self):
        r = contaminated(related=[], understood_as="", interpretation=None, question="q",
                         challenges=[], sides=[], citations=[], steps=[])
        s = r.shareable()
        self.assertEqual(dataclasses.replace(s, question="q"), r)


class FailClosed(unittest.TestCase):
    def test_only_a_shared_response(self):
        for aud in ("personal", ""):
            with self.assertRaises(NotShareable):
                contaminated(audience=aud).shareable()

    def test_a_non_shared_claim_in_a_step(self):
        r = contaminated()
        r.steps[0]["claims"][0]["scope"] = "personal"
        with self.assertRaises(NotShareable):
            r.shareable()
        r = contaminated()
        r.steps[0]["also_unverified"] = [{"id": 2, "text": "KAI", "status": "unverified"}]
        with self.assertRaises(NotShareable):
            r.shareable()            # no scope at all: cannot be established

    def test_a_non_shared_citation(self):
        r = contaminated()
        r.citations[0].scope = "personal"
        with self.assertRaises(NotShareable):
            r.shareable()

    def test_wrong_types(self):
        for bad in ({"ask": 5}, {"value": 5}, {"outcome": ["x"]}):
            r = contaminated()
            r.steps[0].update(bad)
            with self.assertRaises(NotShareable, msg=str(bad)):
                r.shareable()
        with self.assertRaises(NotShareable):
            contaminated(steps=["not a mapping"]).shareable()
        with self.assertRaises(NotShareable):
            contaminated(challenges=["x"]).shareable()

    def test_a_removed_source_title_still_named_in_the_sentence(self):
        r = contaminated(sentence="According to LUIS-SOURCE-TITLE, Mira maintains it.")
        with self.assertRaises(NotShareable):
            r.shareable()

    def test_a_removed_passage_text_still_in_a_kept_field(self):
        r = contaminated(possible="KAI-PERSONAL-PASSAGE")
        with self.assertRaises(NotShareable):
            r.shareable()

    def test_a_field_this_version_does_not_know(self):
        @dataclasses.dataclass
        class Later(Response):
            extra_text: str = "LUIS-SECRET-ROOM"
        base = dataclasses.asdict(contaminated())
        for k in ("citations", "steps"):
            base[k] = getattr(contaminated(), k)
        with self.assertRaises(NotShareable):
            Later(**base).shareable()

    def test_a_save_offer(self):
        with self.assertRaises(NotShareable):
            contaminated(save_offer={"text": "LUIS-SECRET-ROOM"}).shareable()

    def test_not_a_response(self):
        from puzzle_assistant.brain.shareable import sanitize_response
        with self.assertRaises(NotShareable):
            sanitize_response({"audience": "shared"})


class Answers(unittest.TestCase):
    def make(self, **over):
        hits = [Hit("library", "1", "Mira maintains the Lantern Garden.", "verified", "user", 1.0,
                    "", ["1 direct"], {"scope": "shared", "version": 1}),
                Hit("library", "2", "Luis SECRET-VISIT", "unverified", "user", 1.0, "", [],
                    {"scope": "personal"}),
                Hit("library", "3", "KAI-PERSONAL-PASSAGE", "unverified", "user", 1.0, "", [], {}),
                Hit("quiz_memory", "q", "LUIS-SECRET-ROOM", "unverified", "user", 1.0, "",
                    ["LUIS-SOURCE-TITLE"], {"scope": "shared"}),
                Hit("library", "4", "LUIS-SECRET-ROOM", "unverified", "user", 1.0, "", [],
                    {"scope": "proposed"})]
        a = BrainAnswer("Who maintains the LUIS-TYPED-QUESTION?", True, "Mira", "documented",
                        "verified", hits, ["found 5 related records", "LUIS-SECRET-ROOM"],
                        "established", None,
                        [{"ask": "x", "outcome": "value", "value": "Mira", "verified": True,
                          "candidates": [], "claims": [{"id": 1, "text": "t", "status": "verified",
                                                         "scope": "shared"}],
                          "also_unverified": [], "x": "LUIS-SOURCE-TITLE"}], "shared")
        return dataclasses.replace(a, **over) if over else a

    def test_hits_trace_question(self):
        a = self.make()
        before = copy.deepcopy(a)
        s = a.shareable()
        for m in MARKS:
            self.assertNotIn(m, dump(s), m)
        self.assertEqual([h.record_id for h in s.hits], ["1"])
        self.assertEqual((s.question, s.trace, s.answer, s.answered), ("", [], "Mira", True))
        self.assertEqual(s.hits[0].detail, {"scope": "shared"})
        self.assertEqual(a, before)                       # original untouched
        s.hits[0].text = "CHANGED"
        self.assertEqual(a, before)

    def test_only_a_shared_answer(self):
        for aud in ("personal", ""):
            with self.assertRaises(NotShareable):
                self.make(audience=aud).shareable()

    def test_a_personal_claim_in_a_step(self):
        a = self.make()
        a.steps[0]["claims"][0]["scope"] = "personal"
        with self.assertRaises(NotShareable):
            a.shareable()


class RealResponses(World):
    """Real libraries: a shared Established answer, then the asker's own contamination."""
    def setUp(self):
        super().setUp()
        self.ids["rita"] = self.lib.create_user("Rita")
        ana = self.open("ana")
        sid = self.imp(ana, "Mira maintains the Lantern Garden.\n", "ana.txt", publisher="Harbour Press")
        c = ana.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        ana.cite(c, sid, "Mira maintains the Lantern Garden.")
        ana.attest_source(sid, "checked")
        m = self.open("mod")
        sh = m.approve(self.fid("ana"), ana.propose(c, {sid: ["quote", "full_text"]}))
        ssid = m.store._db.execute("SELECT source_id FROM evidence WHERE claim_id=?",
                                   (sh,)).fetchone()[0]
        m.attest_source(ssid, "checked by a moderator")
        m.review(sh, "verified")

    def luis(self, passage=True):
        l = self.open("luis")
        if passage:
            self.imp(l, "Kai maintains the Lantern Garden. KAI-PERSONAL-PASSAGE\n",
                     "LUIS-SOURCE-TITLE.txt", publisher="MY-NOTES-PUBLISHER")
        e = l.add_entity("LUIS-SECRET-ROOM", "room")
        l.approve_alias(l.add_alias(e, "Lantern Gardn"))
        l.learn("Luis visits the Lantern Garden every Monday SECRET-VISIT.", "user", "Lantern Garden")
        return l

    def test_a_personal_challenge_makes_the_response_unshareable(self):
        r = self.luis().respond(Q_MG)
        self.assertEqual((r.category, r.value, r.audience), ("established", "Mira", "personal"))
        with self.assertRaises(NotShareable):
            r.shareable()

    def test_a_shared_response_is_cleaned_and_the_asker_view_is_whole(self):
        l = self.luis(passage=False)
        r = l.respond(Q_MG + " ")
        self.assertEqual((r.category, r.value, r.audience), ("established", "Mira", "shared"))
        self.assertTrue(any("SECRET-VISIT" in x["text"] for x in r.related))   # the asker's own view
        self.assertTrue(r.question)
        before = copy.deepcopy(r)
        s = r.shareable()
        self.assertEqual(r, before)                 # the personal view is untouched
        self.assertEqual((s.question, s.related, s.value), ("", [], "Mira"))
        self.assertTrue(s.citations)
        for m in MARKS:
            self.assertNotIn(m, dump(s), m)

    def test_a_response_for_someone_without_material_is_cleaned_too(self):
        r = self.open("rita").respond(Q_MG)
        self.assertEqual(r.audience, "shared")
        s = r.shareable()
        self.assertEqual((s.question, s.related, s.value), ("", [], "Mira"))

    def test_a_real_ask_answer(self):
        a = self.luis(passage=False).ask(Q_MG)
        self.assertEqual((a.answered, a.audience), (True, "shared"))
        self.assertTrue(any("SECRET-VISIT" in h.text for h in a.hits))
        before = copy.deepcopy(a)
        s = a.shareable()
        self.assertEqual(a, before)
        self.assertEqual((s.question, s.trace), ("", []))
        for m in MARKS:
            self.assertNotIn(m, dump(s), m)
        self.assertTrue(all(h.detail == {"scope": "shared"} for h in s.hits))

    def test_ask_answers_never_carry_passages_and_their_hits_are_dropped(self):
        a = self.luis().ask(Q_MG)       # ask() has no challenges: its answer is shared claims only
        self.assertEqual(a.audience, "shared")
        self.assertTrue(any("SECRET-VISIT" in h.text for h in a.hits))
        s = a.shareable()
        for m in MARKS:
            self.assertNotIn(m, dump(s), m)

    def test_a_personal_answer_cannot_be_prepared(self):
        a = self.luis().ask("Who maintains the Lantern Garden?")
        a = dataclasses.replace(a, audience="personal")
        with self.assertRaises(NotShareable):
            a.shareable()


if __name__ == "__main__":
    unittest.main()
