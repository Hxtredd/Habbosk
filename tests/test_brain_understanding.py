"""B5.1 question understanding: hard gates and guarantees (synthetic data only).

  * the scored set (tests/brain_understanding_eval.py): every variant and
    probe as expected; held-out floor; ZERO wrong answers, ZERO answers from a
    misspelt name, ZERO private names leaked; language 80/80;
  * "Did you mean ...?" is never an answer: no value, no citations, no steps;
    tapping its question gives the normal answer;
  * every clarification choice is a question SK understands exactly;
  * suggestions only ever come from names the asker may see (five askers);
  * understanding never writes, reads only through viewer-checked calls and
    has no network code; ask() / search() are unchanged (the B2 / B3
    references are checked in their own tests);
  * the reused fact index / name list never outlive a change.
"""
import ast
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import brain_understanding_eval as U  # noqa: E402
from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain import understand as UN  # noqa: E402
from puzzle_assistant.brain.model import UNKNOWN_REASONS  # noqa: E402

BRAIN_DIR = os.path.dirname(os.path.abspath(UN.__file__))   # wherever the Brain is installed
HELDOUT_FLOOR = 84          # first run, before any change for it (84/90): never lower


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class ScoredSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = U.evaluate()

    def test_hard_gates(self):
        self.assertEqual(self.res["wrong_answers"], [])
        self.assertEqual(self.res["fuzzy_answers"], [])
        self.assertEqual(self.res["leaks"], [])

    def test_every_variant_and_probe_as_expected(self):
        missed = [(r["q"], r["why"]) for r in self.res["rows"]
                  if r["target"] != "heldout" and not r["ok"]]
        self.assertEqual(missed, [])
        self.assertGreaterEqual(sum(1 for r in self.res["rows"] if r["target"] not in
                                    ("probe", "heldout")), 1000)

    def test_heldout_floor(self):
        held = [r for r in self.res["rows"] if r["target"] == "heldout"]
        self.assertGreaterEqual(sum(r["ok"] for r in held), HELDOUT_FLOOR)

    def test_language(self):
        wrong = [(q, want, got) for q, want, got in self.res["language"] if want != got]
        self.assertEqual(wrong, [])
        self.assertGreaterEqual(len(self.res["language"]), 80)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_b51_")
        self.path = os.path.join(self.d, "k.db")
        self.b, self.users = U.build(self.path)
        self.ana = self.users["ana"]

    def tearDown(self):
        self.b.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def ask(self, q, who=None):
        return self.b.respond(q, viewer=who or self.ana)


class DidYouMean(Base):
    def test_never_an_answer(self):
        for q in ("Who maintains the Lantern Gardn?", "Lantern Gardens caretaker",
                  "¿Quién cuida el Mercado Asul?", "Faro Nort room ID",
                  "Which room does Mria look after?", "Glass Libray opening time"):
            r = self.ask(q)
            self.assertEqual((r.category, r.reason, r.value), ("unknown", "did_you_mean", None), q)
            self.assertEqual((r.citations, r.steps, r.possible, r.sides), ([], [], None, []), q)
            self.assertEqual(r.audience, "personal")
            self.assertEqual(len(r.clarification["options"]), 1)
            self.assertEqual(len(r.clarification["questions"]), 1)

    def test_tapping_the_suggestion_answers(self):
        for q, value in (("Who maintains the Lantern Gardn?", "Mira"),
                         ("¿Quién cuida el Mercado Asul?", "Tomás"),
                         ("Faro Nort room ID", "300401"),
                         ("Which room does Mria look after?", "Lantern Garden"),
                         ("Glass Libray opening time", "nine"),
                         ("¿Cuál es el ID del Jardín Botánco?", "300600")):
            r = self.ask(q)
            tap = self.ask(r.clarification["questions"][0])
            self.assertEqual((tap.category, tap.value), ("established", value), (q, tap.question))
            self.assertFalse(tap.interpretation and tap.interpretation["corrections"])

    def test_wording(self):
        self.assertEqual(self.ask("Who maintains the Lantern Gardn?").sentence,
                         "Did you mean the Lantern Garden?")
        self.assertEqual(self.ask("¿Quién cuida el Mercado Asul?").sentence,
                         "¿Te refieres a Mercado Azul?")
        self.assertEqual(self.ask("Which room does Mria look after?").sentence,
                         "Did you mean Mira?")

    def test_the_reason_is_in_the_fixed_list(self):
        self.assertIn("did_you_mean", UNKNOWN_REASONS)
        # B5.1 added did_you_mean after the B4 reasons (B5.2 then added evidence_withdrawn)
        self.assertEqual(UNKNOWN_REASONS.index("did_you_mean"), 6)


class UnderstoodAs(Base):
    def test_shown_with_corrections(self):
        r = self.ask("Who mantains the Lantern Garden?")
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        self.assertEqual(r.understood_as, 'Understood as: who looks after the Lantern Garden '
                                          '("mantains" read as "maintains").')
        self.assertEqual(r.interpretation["corrections"], [{"from": "mantains", "to": "maintains"}])
        r = self.ask("¿Quién mantine el Mercado Azul?")
        self.assertTrue(r.understood_as.startswith("Entendido como: quién cuida Mercado Azul"))

    def test_shown_for_another_word_order(self):
        r = self.ask("Lantern Garden, who maintains it?")
        self.assertEqual(r.understood_as, "Understood as: who looks after the Lantern Garden.")
        self.assertEqual(r.interpretation["relation"], "caretaker")
        self.assertEqual(r.interpretation["corrections"], [])

    def test_not_shown_for_b3s_own_questions(self):
        for q in ("Who maintains the Lantern Garden?", "What is the room ID of the Faro Norte?",
                  "¿Cuántas monedas da la misión del Mercado Azul?"):
            r = self.ask(q)
            self.assertEqual((r.understood_as, r.interpretation), ("", None), q)


class Clarifications(Base):
    def test_every_choice_is_understood_exactly(self):
        for q in ("Faro room id", "What is the room ID of the Faro?", "Twin Lights room ID",
                  "Who maintains the Torre Alda?", "Which room does Nora look after?",
                  "¿Cuál es el ID del Faro?", "Who is in charge of the Lantern Garden?",
                  "What is the room ID of the Twin Lights?"):
            r = self.ask(q)
            self.assertEqual(r.reason, "ambiguous", q)
            qs = r.clarification["questions"] if "questions" in r.clarification else []
            if q == "Which room does Nora look after?":
                self.assertEqual(qs, [])            # a reverse question's places: names only
                continue
            self.assertEqual(len(qs), len(r.clarification["options"]), q)
            for choice in qs:
                t = self.ask(choice)
                self.assertNotIn(t.reason, ("not_understood", "did_you_mean", "ambiguous"),
                                 (q, choice))
                self.assertFalse(t.interpretation and t.interpretation["corrections"])


class Privacy(Base):
    """Five askers: nobody is ever offered a name they may not see."""

    def setUp(self):
        super().setUp()
        self.eve = self.b.store.add_contributor("Eve (eval)", "external_user")
        self.askers = {"ana": self.ana, "luis": self.users["luis"], "mod": self.users["mod"],
                       "eve": self.eve, "nobody": "nobody-at-all"}

    def test_rejected_names_and_proposed_aliases_are_not_names(self):
        names = set(self.b._known_names(self.ana).by_key)
        self.assertNotIn("ghost hall", names)
        self.assertNotIn("cordage hall", names)
        self.assertIn("faro del norte", names)              # an approved alias is

    def test_names_are_only_what_the_asker_may_see(self):
        ana = set(self.b._known_names(self.ana).by_key)
        luis = set(self.b._known_names(self.users["luis"]).by_key)
        self.assertIn("secret cellar", ana)
        self.assertIn("hidden vault", luis)
        for who in ("luis", "mod", "eve", "nobody"):
            names = set(self.b._known_names(self.askers[who]).by_key)
            self.assertNotIn("secret cellar", names, who)
            self.assertNotIn("zeta", names, who)
        for who in ("ana", "mod", "eve", "nobody"):
            names = set(self.b._known_names(self.askers[who]).by_key)
            self.assertNotIn("hidden vault", names, who)
            self.assertNotIn("kai", names, who)

    def test_misspelt_private_names_are_never_suggested(self):
        secrets = {"ana": ["Secret Cellar", "Zeta"], "luis": ["Hidden Vault", "Kai"]}
        for owner, words in secrets.items():
            for word in words:
                for typo in U.name_typos(word) + [word]:
                    for q in (f"Who maintains the {typo}?", f"{typo} room ID",
                              f"Which room does {typo} look after?", f"¿Quién cuida el {typo}?"):
                        for who, viewer in self.askers.items():
                            if who == owner:
                                continue
                            r = self.ask(q, viewer)
                            blob = repr((r.sentence, r.clarification, r.understood_as,
                                         r.interpretation, r.value, r.possible, r.steps,
                                         [c.text for c in r.citations]))
                            for w in words:
                                if w.lower() not in q.lower():
                                    self.assertNotIn(w.lower(), blob.lower(), (q, who))
                            self.assertNotEqual(r.reason, "did_you_mean", (q, who))

    def test_the_owner_gets_their_own_suggestion(self):
        self.assertEqual(self.ask("Who maintains the Secret Celar?").clarification["options"],
                         ["Secret Cellar"])
        self.assertEqual(self.ask("Who maintains the Hiden Vault?", self.users["luis"])
                         .clarification["options"], ["Hidden Vault"])

    def test_suggestions_are_personal(self):
        self.assertEqual(self.ask("Who maintains the Lantern Gardn?").audience, "personal")


class ReadOnlyAndBoundaries(Base):
    QUESTIONS = ["Lantern Garden, who maintains it?", "Who mantains the Lantern Garden?",
                 "Who maintains the Lantern Gardn?", "Faro room id", "¿Qué sala cuida Mira?",
                 "Who is in charge of the Lantern Garden?", "Glass Library opening time",
                 "Who doesn't maintain the Lantern Garden?", "¿Cuándo abre la Clock Tower?"]

    def test_understanding_never_writes(self):
        self.ask("warm up")
        before = (_sha(self.path), self.b.store.data_version())
        for q in self.QUESTIONS + [q for q, _, _ in U.PROBES]:
            self.ask(q)
        self.assertEqual((_sha(self.path), self.b.store.data_version()), before)

    def test_every_write_method_can_be_disabled(self):
        names = ("add_claim", "add_source", "add_section", "add_evidence", "add_contributor",
                 "review", "correct", "propose_correction", "propose_share", "approve_share",
                 "link", "_event", "_index_claim", "_set_meta", "add_relation", "add_entity",
                 "add_alias", "approve_alias", "link_entities", "unlink_entities")
        with mock.patch.multiple(type(self.b.store), **{
                n: mock.Mock(side_effect=AssertionError(n)) for n in names
                if hasattr(type(self.b.store), n)}):
            for q in self.QUESTIONS:
                self.ask(q)

    def test_understand_does_not_touch_the_store(self):
        for name in ("understand.py", "understand_words.py"):
            tree = ast.parse(open(os.path.join(BRAIN_DIR, name), encoding="utf-8").read())
            attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            self.assertFalse({"store", "_db", "execute", "commit"} & (attrs | names), name)
            imports = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                       for a in n.names} | {n.module or "" for n in ast.walk(tree)
                                            if isinstance(n, ast.ImportFrom)}
            for bad in ("socket", "ssl", "http", "urllib", "requests", "subprocess", "sqlite3"):
                self.assertFalse(any(i == bad or i.startswith(bad + ".") for i in imports), name)

    def test_ask_and_search_never_use_understanding(self):
        with mock.patch.object(UN, "understand", side_effect=AssertionError("understand")):
            for q in self.QUESTIONS:
                self.b.ask(q, viewer=self.ana)
                self.b.search(q, viewer=self.ana)
        a = self.b.ask("Lantern Garden, who maintains it?", viewer=self.ana)
        self.assertEqual(a.status, "not_reasoned")         # unchanged: B3 does not read it


class ReuseNeverOutlivesAChange(Base):
    def test_a_new_claim_a_review_and_an_alias_are_seen_at_once(self):
        self.assertEqual(self.ask("Old Lamp room ID").reason, "not_understood")
        c = self.b.learn("The Old Lamp has room ID 300900.", "imported", "Old Lamp")
        self.assertEqual((self.ask("Old Lamp room ID").category,
                          self.ask("Old Lamp room ID").reason), ("unknown", "unsupported"))
        self.b.add_evidence(c, "direct", self.b.add_source("document", title="t"), "x")
        self.b.review(c, "verified", reviewer=self.ana)
        self.assertEqual(self.ask("Old Lamp room ID").value, "300900")
        self.assertEqual(self.ask("Who maintains the Old Lamp?").reason, "nothing_documented")
        self.assertEqual(self.ask("Lamplight room ID").reason, "not_understood")
        e = self.b.add_entity("Old Lamp", "room")
        self.b.approve_alias(self.b.add_alias(e, "Lamplight"))
        self.assertEqual(self.ask("Lamplight room ID").value, "300900")
        self.b.review(c, "rejected", reviewer=self.ana)
        self.assertNotEqual(self.ask("Old Lamp room ID").value, "300900")

    def test_a_change_by_another_program_is_seen(self):
        self.assertEqual(self.ask("Lantern Garden, who maintains it?").value, "Mira")
        con = sqlite3.connect(self.path)
        try:
            con.execute("UPDATE claims SET status='rejected' WHERE text LIKE 'Mira maintains%'")
            con.commit()
        finally:
            con.close()
        r = self.ask("Lantern Garden, who maintains it?")
        self.assertNotEqual(r.value, "Mira")

    def test_one_viewers_view_is_never_another_viewers(self):
        self.assertEqual(self.ask("Who maintains the Secret Cellar?").value, "Zeta")
        r = self.ask("Who maintains the Secret Cellar?", self.users["luis"])
        self.assertIsNone(r.value)
        self.assertEqual(self.ask("Secret Cellar caretaker").value, "Zeta")
        self.assertIsNone(self.ask("Secret Cellar caretaker", self.users["luis"]).value)


class NegationOnItsOwn(Base):
    """The negation rule holds even if a negation word were ever an explained
    word (it is checked first, on its own)."""

    def test_negations_refused_even_when_explained(self):
        from puzzle_assistant.brain import understand_words as W
        from puzzle_assistant.brain.text import NEGATIONS
        names, vocab = self.b._known_names(self.ana), self.b._vocab()
        with mock.patch.object(W, "FILLER", W.FILLER | NEGATIONS | {"doesn", "isn", "t"}):
            for q in ("Who does not maintain the Lantern Garden?", "Who never runs the Plaza del "
                      "Puerto?", "¿Quién no cuida el Lantern Garden?", "Lantern Garden: who "
                      "isn't the caretaker?", "¿Quién nunca cuida el Mercado Azul?"):
                self.assertIsNone(UN.understand(q, names, vocab), q)
            self.assertIsNotNone(UN.understand("Who maintains the Lantern Garden, please?", names,
                                               vocab))


class Rules(unittest.TestCase):
    def test_typo_bounds(self):
        self.assertEqual([UN.bound(w, w) for w in ("abc", "abcd", "abcdefg", "abcdefgh")],
                         [0, 1, 1, 2])
        self.assertEqual(UN.bound("room1", "room2"), 0)          # numbers: exact only
        self.assertTrue(UN.close("mantains", "maintains"))
        self.assertTrue(UN.close("maintians", "maintains"))      # swapped letters count 1
        self.assertFalse(UN.close("mra", "mira"))                # 3 letters: exact only
        self.assertTrue(UN.close("gardn", "garden"))
        self.assertFalse(UN.close("owns", "runs"))
        self.assertFalse(UN.close("300101", "300100"))
        self.assertEqual(UN.distance("abcdef", "badcfe", 1), 2)   # stops early: more than 1

    def test_language_scores(self):
        for q, lang in (("¿Quién mantiene el Jardín Nube?", "es"), ("Quien cuida la Plaza", "es"),
                        ("¡Hola!", "es"), ("Who maintains the Plaza del Puerto?", "en"),
                        ("room number of the Sala Azul", "en"), ("", "en"),
                        ("Faro del Norte room ID", "en"), ("encargado del Lantern Garden", "es")):
            self.assertEqual(UN.language(q), lang, q)

    def test_canonical_questions_are_understood_exactly(self):
        d = tempfile.mkdtemp(prefix="sk_b51_canon_")
        b, users = U.build(os.path.join(d, "k.db"))
        try:
            vocab = b._vocab()
            for tid, lang, rel, name, (cat, value) in U.TARGETS:
                relation, q, inv = rel, "", False
                if rel.startswith("leads_"):
                    relation, q = "leads_to", rel.split("_")[1]
                if rel == "inverse":
                    relation, inv = "caretaker", True
                text = UN.question_for(relation, name, lang, vocab, q, inv)
                r = b.respond(text, viewer=users["ana"])
                self.assertEqual((r.category, r.value), (cat, value), (tid, text))
                self.assertFalse(r.interpretation and r.interpretation["corrections"], text)
                self.assertEqual(r.language, lang, text)
        finally:
            b.close()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
