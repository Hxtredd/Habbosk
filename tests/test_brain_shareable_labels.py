"""B6.3: what shareable() keeps of a step label, a value and a sentence.

An independent review (ChatGPT, of 68b500f) asked whether `steps[].ask` - built from names the
asker typed - can carry the asker's personal names into a response passed on. Findings of this
work: (1) in every realistic flow tried, a response marked shared has labels made only of the
fixed wording and names from SHARED claims (the audience and the place-name rules guarantee it);
a label that holds a personal alias only occurs on answers that are not shared. (2) shareable()
nevertheless trusted the label text. These tests pin the new rule: every word of a label, value,
possible value and sentence must come from the response's own shared material or the fixed
wording, otherwise NotShareable."""
import dataclasses
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from tests.test_brain_b6 import World  # noqa: E402
from tests.test_brain_shareable import contaminated, dump  # noqa: E402
from puzzle_assistant.brain.shareable import NotShareable  # noqa: E402

PERSONAL = ("luis-secret", "luis secret", "luis-nick", "luis nick", "secret-visit")


def has_personal(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in PERSONAL)


class HandBuiltLabels(unittest.TestCase):
    """A shared-marked response whose label, value or sentence names something that is not in
    its shared material. Before this change shareable() passed all of these through."""

    def test_a_label_naming_the_askers_alias_is_refused(self):
        for ask in ("the room whose caretaker is luis nick for mira",
                    "who looks after it (LUIS-SECRET-ROOM)",
                    "who looks after it (Lantern Garden, Luis' hideout)",
                    "where it leads (the place called LUIS-SECRET-ROOM)"):
            r = contaminated()
            r.steps[0]["ask"] = ask
            with self.assertRaises(NotShareable, msg=ask):
                r.shareable()

    def test_a_step_value_value_possible_and_sentence_are_checked_too(self):
        for over in ({"value": "LUIS-SECRET-ROOM"}, {"possible": "LUIS-SECRET-ROOM"},
                     {"sentence": "LUIS-SECRET-ROOM maintains it."}):
            with self.assertRaises(NotShareable, msg=str(over)):
                contaminated(**over).shareable()
        r = contaminated()
        r.steps[0]["value"] = "Luis Nick"
        with self.assertRaises(NotShareable):
            r.shareable()

    def test_legitimate_labels_pass(self):
        r = contaminated()
        for ask in ("who looks after it (Lantern Garden)", "the room whose caretaker is mira",
                    "where it leads (Lantern Garden, left side)",
                    "where the exit leads (no place named; the only one documented is Lantern Garden)"):
            r.steps[0]["ask"] = ask
            self.assertEqual(r.shareable().steps[0]["ask"], ask)

    def test_the_answer_object_is_checked_too(self):
        from tests.test_brain_shareable import Answers
        a = Answers().make()
        a.steps[0]["ask"] = "the room whose caretaker is luis nick for mira"
        with self.assertRaises(NotShareable):
            a.shareable()
        a = dataclasses.replace(Answers().make(), answer="Luis Nick")
        with self.assertRaises(NotShareable):
            a.shareable()


class RealFlows(World):
    """Real libraries, real questions (English and Spanish; forward, reverse, typos, the asker's
    own aliases typed). Every response marked shared must pass shareable() with no personal
    word anywhere; every other response must be refused."""
    def setUp(self):
        super().setUp()
        self.ids["rita"] = self.lib.create_user("Rita")
        ana = self.open("ana")
        self.shared = []
        for text, subject in (("Mira maintains the Lantern Garden.", "Lantern Garden"),
                              ("The exit of the Lantern Garden leads to the Rope Shed.", "Lantern Garden"),
                              ("Odo maintains the Rope Shed.", "Rope Shed")):
            sid = self.imp(ana, text + "\n", "ana-%d.txt" % len(self.shared), publisher="Harbour Press")
            c = ana.learn(text, "imported", subject)
            ana.cite(c, sid, text)
            ana.attest_source(sid, "checked")
            m = self.open("mod")
            sh = m.approve(self.fid("ana"), ana.propose(c, {sid: ["quote", "full_text"]}))
            ssid = m.store._db.execute("SELECT source_id FROM evidence WHERE claim_id=?", (sh,)).fetchone()[0]
            m.attest_source(ssid, "checked by a moderator")
            m.review(sh, "verified")
            self.shared.append(sh)

    QUESTIONS = [
        "Who maintains the Lantern Garden?", "who looks after the lantern garden",
        "Who maintains the Rope Shed?", "Which room does Mira maintain?", "which room does odo look after",
        "Where does the exit of the Lantern Garden lead?", "Who maintains the room the Lantern Garden leads to?",
        "Who looks after the room that the Lantern Garden leads to?",
        "Quien cuida la Lantern Garden?", "Que sala cuida Mira?", "Who maintains the Lantern Gardn?",
        "Who maintains the LUIS-SECRET-ROOM?", "Who maintains the LUIS-SECRET-ROOMM?",
        "Which room does LUIS-NICK-FOR-MIRA maintain?", "Where does the exit of the LUIS-SECRET-ROOM lead?",
        "Who looks after the room that LUIS-NICK-FOR-MIRA looks after?",
    ]

    def run_all(self, who, label):
        s = self.open(who)
        shared = refused = 0
        for q in self.QUESTIONS:
            r = s.respond(q)
            if r.audience == "shared":
                out = r.shareable()                       # must not raise
                self.assertFalse(has_personal(dump(out)), (label, q, "personal word kept"))
                shared += 1
            else:
                with self.assertRaises(NotShareable, msg=(label, q)):
                    r.shareable()
                refused += 1
        return shared, refused

    def personal_world(self, aliases):
        l = self.open("luis")
        if aliases:
            e = l.add_entity("Mira", "person"); l.approve_alias(l.add_alias(e, "LUIS-NICK-FOR-MIRA"))
            e2 = l.add_entity("Lantern Garden", "room"); l.approve_alias(l.add_alias(e2, "LUIS-SECRET-ROOM"))
        l.learn("Luis visits the Lantern Garden every Monday SECRET-VISIT.", "user", "Lantern Garden")

    def test_someone_with_no_material_of_their_own(self):
        shared, refused = self.run_all("rita", "rita")
        self.assertGreaterEqual(shared, 8)                      # not vacuous
        self.assertGreater(refused, 0)

    def test_an_asker_with_personal_aliases_and_claims(self):
        self.personal_world(aliases=True)
        shared, refused = self.run_all("luis", "luis-aliases")
        self.assertGreater(shared, 5)
        self.assertGreater(refused, 0)

    def test_an_asker_with_only_a_personal_claim(self):
        self.personal_world(aliases=False)
        shared, refused = self.run_all("luis", "luis-claim")
        self.assertGreaterEqual(shared, 8)

    def test_a_label_with_the_askers_alias_is_never_on_a_shared_response(self):
        """The reviewer's scenario, traced: the inverse label holds the TYPED name's key; it only
        appears on answers that are not shared (no shared claim carries that key)."""
        self.personal_world(aliases=True)
        r = self.open("luis").respond("Which room does LUIS-NICK-FOR-MIRA maintain?")
        self.assertIn("luis nick for mira", " ".join(st["ask"] for st in r.steps))
        self.assertNotEqual(r.audience, "shared")
        with self.assertRaises(NotShareable):
            r.shareable()


if __name__ == "__main__":
    unittest.main()
