"""B3: connected facts, documented absence, synonym gaps - and B2 unchanged."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain import retrieve as _retrieve  # noqa: E402
from puzzle_assistant.brain.facts import extract, key  # noqa: E402
from puzzle_assistant.brain.reason import plan  # noqa: E402
from puzzle_assistant.brain.text import absence_terms, terms  # noqa: E402

_retrieve.VERIFY_FAST = True      # every search here also checks fast == full scoring


class ExtractionTests(unittest.TestCase):
    def f(self, text, subject=""):
        x = extract(text, subject)
        return None if x is None else (x.relation, x.place, x.qualifier, x.value)

    def test_each_relation_in_english_and_spanish(self):
        cases = [
            ("The north door of the Plaza Mayor leads to the Jardín de las Rosas.",
             ("leads_to", "plaza mayor", "north", "jardin de las rosas")),
            ("From the Bosque Oscuro the west path leads to the Cementerio Viejo.",
             ("leads_to", "bosque oscuro", "west", "cementerio viejo")),
            ("La salida norte del Puerto Coral lleva al Faro del Norte.",
             ("leads_to", "puerto coral", "north", "faro del norte")),
            ("The Jardín de la Luna connects to the Biblioteca Antigua through the archway.",
             ("leads_to", "jardin de la luna", "", "biblioteca antigua")),
            ("The Cueva Marina can only be reached from the Faro del Sur.",
             ("leads_to", "faro del sur", "", "cueva marina")),
            ("Marta is the caretaker of the Jardín de las Rosas.",
             ("caretaker", "jardin de las rosas", "", "marta")),
            ("Tomás maintains the Biblioteca Antigua.", ("caretaker", "biblioteca antigua", "", "tomas")),
            ("Rafael looks after the Faro del Sur.", ("caretaker", "faro del sur", "", "rafael")),
            ("El encargado del Mercado Central es Don Tito.",
             ("caretaker", "mercado central", "", "don tito")),
            ("Lucía cuida la Torre del Reloj.", ("caretaker", "torre del reloj", "", "lucia")),
            ("Capitana Irene runs the Puerto Coral.", ("runs", "puerto coral", "", "capitana irene")),
            ("The Plaza Mayor has room ID 100201.", ("room_id", "plaza mayor", "", "100201")),
            ("El Faro del Sur tiene el ID 2104553.", ("room_id", "faro del sur", "", "2104553")),
            ("La misión del Faro del Norte da 30 monedas.", ("quest_reward", "faro del norte", "", "30")),
            ("The quest in the Faro del Sur gives 50 coins.", ("quest_reward", "faro del sur", "", "50")),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(self.f(text), want)

    def test_documented_absence(self):
        self.assertEqual(self.f("The Sala Azul has no caretaker."), ("caretaker", "sala azul", "", None))
        self.assertEqual(self.f("La Cueva Marina no tiene encargado."),
                         ("caretaker", "cueva marina", "", None))
        self.assertEqual(self.f("Nobody looks after the Old Pier."), ("caretaker", "old pier", "", None))
        self.assertEqual(self.f("The Cementerio Viejo has no permanent room ID; it is far."),
                         ("room_id", "cementerio viejo", "", None))

    def test_place_from_the_subject_only_when_the_text_has_none(self):
        self.assertEqual(self.f("The northern exit leads to Crystal Courtyard.", "Welcome Hall"),
                         ("leads_to", "welcome hall", "north", "crystal courtyard"))
        self.assertIsNone(self.f("The northern exit leads to Crystal Courtyard."))   # whose exit?

    def test_no_fact_is_guessed(self):
        for text in ("The taxi works in every room.", "Pets are not allowed in the Hotel Coral.",
                     "The fountain in the Plaza Mayor is working.", "The Sala Azul quest is free.",
                     "Marta and Leo are the caretakers of the Jardín.",
                     "The Plaza Mayor is next to the Sala Azul.",       # not a connection
                     "The Plaza Mayor and the Sala Azul are in the same guide.",
                     "Mira is the caretaker of the room that is north.",
                     "The Plaza Mayor has room ID.", ""):
            with self.subTest(text=text):
                self.assertIsNone(self.f(text))
        # the place is the shop, not the room it is in
        self.assertEqual(self.f("Leo runs the shop in the Plaza Mayor.")[1], "shop in the plaza mayor")

    def test_uncertain_negated_and_indefinite_claims_state_no_fact(self):
        for text in ("The north door of the Plaza Mayor no longer leads to the Jardín.",
                     "La salida norte del Puerto Coral ya no lleva al Faro del Norte.",
                     "The north door of the Plaza Mayor might lead to the Jardín.",
                     "I think Mira maintains the Crystal Courtyard.",
                     "Maybe Mira maintains the Crystal Courtyard.",
                     "Mira used to maintain the Crystal Courtyard.",
                     "Mira will maintain the Crystal Courtyard next week.",
                     "Mira does not maintain the Crystal Courtyard.",
                     "Marta no es la encargada del Faro del Norte.",
                     "Creo que Rafa es el encargado del Faro del Norte.",
                     "Rafa probablemente cuida el Faro del Norte.",
                     "Someone maintains the Crystal Courtyard.",
                     "The new caretaker maintains the Crystal Courtyard.",
                     "The north door leads to the garden."):           # a common noun
            with self.subTest(text=text):
                self.assertIsNone(self.f(text, "Plaza Mayor"))

    def test_names_as_written(self):
        self.assertEqual(self.f("Marta van der Berg maintains the Rose Garden."),
                         ("caretaker", "rose garden", "", "marta van der berg"))
        self.assertEqual(extract("El encargado del Mercado Central es Don Tito.").display, "Don Tito")
        self.assertEqual(self.f("Capitana Irene runs the Puerto Coral."),
                         ("runs", "puerto coral", "", "capitana irene"))

    def test_similar_names_stay_distinct(self):
        self.assertNotEqual(key("the Faro del Norte"), key("Faro del Sur"))
        self.assertEqual(key("the Faro del Norte"), key("Faro del Norte"))


def _build():
    """A small harbour for reasoning: 4 people, verified / unverified /
    conflicting / private / shared claims. Returns (brain, ids, users)."""
    d = tempfile.mkdtemp()
    b = Brain(os.path.join(d, "k.db"), "", "")
    st = b.store
    u = {"ana": b.local_user, "luis": st.add_contributor("Luis", "external_user"),
         "marta": st.add_contributor("Marta", "external_user"),
         "mod": st.add_contributor("Mod", "moderator")}
    ids = {}

    def claim(k, text, subject, who="ana", plan_="verified"):
        c = b.learn(text, "user", subject, by=u[who])
        ids[k] = c
        if plan_ in ("verified", "shared"):
            s = b.add_source("screenshot", title=k, reference="s://" + k, by=u[who])
            b.add_evidence(c, "direct", s, by=u[who])
            b.review(c, "verified", reviewer=u[who])
            if plan_ == "shared":
                b.propose_share(c, by=u[who])
                b.review(c, "verified", reviewer=u["mod"])
                b.approve_share(c, u["mod"])
        elif plan_ in ("rejected", "outdated", "awaiting_verification"):
            b.review(c, plan_, reviewer=u[who])
        return c

    # Ana's verified chain
    claim("hall_north", "The northern exit leads to Crystal Courtyard.", "Welcome Hall")
    claim("mira", "Mira maintains Crystal Courtyard.", "Crystal Courtyard")
    claim("court_id", "Crystal Courtyard has room ID 300100.", "Crystal Courtyard")
    claim("court_east", "The east gate of Crystal Courtyard leads to the Glass Pier.", "Crystal Courtyard")
    claim("pier_id", "The Glass Pier has room ID 300200.", "Glass Pier")
    # Spanish, verified
    claim("puerto_norte", "La salida norte del Puerto Coral lleva al Faro del Norte.", "Puerto Coral")
    claim("rafa", "Rafa es el encargado del Faro del Norte.", "Faro del Norte")
    claim("quest_norte", "La misión del Faro del Norte da 30 monedas.", "Faro del Norte")
    # unverified link
    claim("rafael", "Rafael is the caretaker of the Faro del Sur.", "Faro del Sur", plan_="submitted")
    claim("quest_sur", "La misión del Faro del Sur da 50 monedas.", "Faro del Sur")
    # documented absence vs nothing documented
    claim("pier_nocare", "The Glass Pier has no caretaker.", "Glass Pier")
    claim("cueva_id", "La Cueva Marina tiene el ID 2104570.", "Cueva Marina")
    # conflict: two verified caretakers of the Old Market
    claim("market_a", "Leo maintains the Old Market.", "Old Market")
    claim("market_b", "Ines maintains the Old Market.", "Old Market")
    claim("gate_market", "The south gate of the Old Square leads to the Old Market.", "Old Square")
    # explicit contradiction (statuses become 'conflicting')
    claim("tower_a", "Bruno maintains the Clock Tower.", "Clock Tower", plan_="submitted")
    claim("tower_b", "Carla maintains the Clock Tower.", "Clock Tower", plan_="submitted")
    b.link(ids["tower_b"], ids["tower_a"], "contradicts")
    # rejected / outdated never used
    claim("old_id", "The Glass Pier has room ID 300199.", "Glass Pier", plan_="outdated")
    claim("wrong_care", "Pablo maintains the Glass Pier.", "Glass Pier", plan_="rejected")
    # same source, no relation: must never connect
    claim("guide1", "The Sun Room is listed in the harbour guide.", "Sun Room")
    claim("guide2", "Pilar is mentioned in the harbour guide.", "Pilar")
    # private knowledge of others
    claim("luis_exit", "The west door of the Old Square leads to Luis Den.", "Old Square", who="luis")
    claim("luis_den", "Luis maintains Luis Den.", "Luis Den", who="luis")
    claim("luis_court", "Crystal Courtyard has room ID 999999.", "Crystal Courtyard", who="luis")
    claim("marta_prop", "Marta maintains the Sun Room.", "Sun Room", who="marta", plan_="verified")
    b.propose_share(ids["marta_prop"], by=u["marta"])                  # proposal, unreviewed
    # shared library (everyone)
    claim("lib_exit", "The north door of the Town Hall leads to the Rose Garden.", "Town Hall",
          who="mod", plan_="shared")
    claim("lib_care", "Olga maintains the Rose Garden.", "Rose Garden", who="mod", plan_="shared")
    claim("lib_id", "The Rose Garden has room ID 400100.", "Rose Garden", who="mod", plan_="shared")
    # Ana's private link into the shared library
    claim("ana_gate", "The east gate of the Glass Pier leads to the Rose Garden.", "Glass Pier")
    return b, ids, u, d


class ReasoningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b, cls.ids, cls.u, cls.dir = _build()

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def ask(self, q, who="ana"):
        return self.b.ask(q, viewer=self.u[who])

    def used(self, r):
        return [c["id"] for st in r.steps for c in st["claims"]]

    # -- positive --------------------------------------------------------------
    def test_the_spec_example(self):
        d = tempfile.mkdtemp()
        b = Brain(os.path.join(d, "k.db"), "", "")
        try:
            ids = []
            for text, subject in (("The northern exit leads to Crystal Courtyard.", "Welcome Hall"),
                                  ("Mira maintains Crystal Courtyard.", "Crystal Courtyard")):
                c = b.learn(text, "user", subject)
                s = b.add_source("screenshot", title=text, reference="s://" + text)
                b.add_evidence(c, "direct", s)
                b.review(c, "verified")
                ids.append(c)
            r = b.ask("Who maintains the room the northern exit leads to?")
            self.assertEqual((r.status, r.answered, r.answer), ("established", True, "Mira"))
            self.assertEqual(self.used(r), ids)
            self.assertEqual(r.basis, "derived")
            self.assertTrue(any("connected through the same place name" in t for t in r.trace))
        finally:
            b.close()
            shutil.rmtree(d, ignore_errors=True)

    def test_an_unnamed_exit_with_several_candidates_is_ambiguous(self):
        # three places document a north exit: which one is meant is not known
        r = self.ask("Who maintains the room the northern exit leads to?")
        self.assertEqual((r.status, r.answered), ("ambiguous", False))
        self.assertEqual(sorted(r.steps[0]["candidates"]), ["Puerto Coral", "Town Hall",
                                                             "Welcome Hall"])
        r = self.ask("Who maintains the room the north exit of the Welcome Hall leads to?")
        self.assertEqual((r.status, r.answer), ("established", "Mira"))

    def test_two_facts_in_spanish(self):
        r = self.ask("¿Quién es el encargado de la sala a la que lleva la salida norte del Puerto Coral?")
        self.assertEqual((r.status, r.answer), ("established", "Rafa"))
        r = self.ask("¿Cuántas monedas da la misión de la sala a la que lleva la salida norte del "
                     "Puerto Coral?")
        self.assertEqual((r.status, r.answer), ("established", "30 coins"))

    def test_three_facts(self):
        r = self.ask("What is the room ID of the room the east gate of the room the north exit of "
                     "the Welcome Hall leads to leads to?")
        self.assertEqual((r.status, r.answer), ("established", "300200"))
        self.assertEqual(len(r.steps), 3)

    def test_inverse_step(self):
        r = self.ask("What is the room ID of the room Mira maintains?")
        self.assertEqual((r.status, r.answer), ("established", "300100"))

    def test_single_fact(self):
        r = self.ask("Who maintains Crystal Courtyard?")
        self.assertEqual((r.status, r.answer, r.basis), ("established", "Mira", "documented"))

    # -- unsupported -------------------------------------------------------------
    def test_unverified_link_is_not_an_answer(self):
        r = self.ask("How many coins does the quest give in the room Rafael looks after?")
        self.assertEqual((r.status, r.answered, r.answer), ("unverified", False, None))
        self.assertEqual(r.possible, "50 coins")
        self.assertTrue(any("not established" in t for t in r.trace))

    def test_missing_link_cannot_be_established(self):
        for q in ("Who maintains the room the western exit leads to?",   # only Luis's (private)
                  "What is the room ID of the room Nora maintains?",
                  "Who maintains the Cueva Marina?",                 # ID documented, caretaker not
                  "Who runs Crystal Courtyard?"):
            with self.subTest(q=q):
                r = self.ask(q)
                self.assertEqual((r.status, r.answered, r.answer, r.possible),
                                 ("missing", False, None, None))

    def test_missing_is_never_absence(self):
        r = self.ask("Who maintains the Cueva Marina?")
        self.assertTrue(any("not proof that there is none" in t for t in r.trace))
        self.assertNotIn("none", (r.answer or "") + (r.possible or ""))

    def test_documented_absence(self):
        r = self.ask("Who maintains the Glass Pier?")
        self.assertEqual(r.status, "absent")
        self.assertTrue(r.answered)
        self.assertIn("The Glass Pier has no caretaker.", r.answer)
        r = self.ask("Who maintains the room the east gate of Crystal Courtyard leads to?")
        self.assertEqual(r.status, "absent")                 # chained, ends in an absence

    def test_two_values_conflict(self):
        r = self.ask("Who maintains the room the south gate of the Old Square leads to?")
        self.assertEqual((r.status, r.answered, r.answer), ("conflicting", False, None))
        self.assertEqual(sorted(r.steps[-1]["candidates"]), ["Ines", "Leo"])

    def test_contradicted_claims_conflict(self):
        r = self.ask("Who maintains the Clock Tower?")
        self.assertEqual((r.status, r.answered), ("conflicting", False))

    def test_absence_against_a_value_conflicts(self):
        b, ids, u, d = _build()
        try:
            c = b.learn("Pablo maintains the Glass Pier.", "user", "Glass Pier")
            s = b.add_source("screenshot", title="p", reference="s://pablo")
            b.add_evidence(c, "direct", s)
            b.review(c, "verified")
            self.assertEqual(b.ask("Who maintains the Glass Pier?").status, "conflicting")
        finally:
            b.close()
            shutil.rmtree(d, ignore_errors=True)

    def _small(self, claims, links=()):
        d = tempfile.mkdtemp()
        b = Brain(os.path.join(d, "k.db"), "", "")
        self.addCleanup(shutil.rmtree, d, True)
        self.addCleanup(b.close)
        ids = []
        for text, subject in claims:
            c = b.learn(text, "user", subject)
            s = b.add_source("screenshot", title=text, reference="s://" + text)
            b.add_evidence(c, "direct", s)
            b.review(c, "verified")
            ids.append(c)
        for x, y in links:
            b.link(ids[x], ids[y], "contradicts")
        return b, ids

    def test_contradicted_by_a_claim_without_a_fact(self):
        # the only caretaker fact is contradicted by a claim that states no fact
        b, ids = self._small([("Bruno maintains the Bell Tower.", "Bell Tower"),
                              ("Bruno does not maintain the Bell Tower.", "Bell Tower")],
                             links=[(1, 0)])
        self.assertEqual(b.claim(ids[0])["status"], "conflicting")
        r = b.ask("Who maintains the Bell Tower?")
        self.assertEqual((r.status, r.answered, r.answer, r.possible),
                         ("conflicting", False, None, None))

    def test_a_person_looking_after_two_rooms_is_ambiguous(self):
        b, ids = self._small([("Mira maintains the Rose Garden.", "Rose Garden"),
                              ("Mira maintains the Bell Tower.", "Bell Tower"),
                              ("The Rose Garden has room ID 500100.", "Rose Garden")])
        r = b.ask("What is the room ID of the room Mira maintains?")
        self.assertEqual((r.status, r.answered), ("ambiguous", False))
        self.assertEqual(sorted(r.steps[0]["candidates"]), ["Bell Tower", "Rose Garden"])

    def test_partial_and_similar_names(self):
        r = self.ask("Who is the caretaker of the Faro?")
        self.assertEqual((r.status, r.answered), ("ambiguous", False))
        self.assertEqual(sorted(r.steps[0]["candidates"]), ["Faro del Norte", "Faro del Sur"])
        self.assertEqual(self.ask("Who is the caretaker of the Faro del Norte?").answer, "Rafa")
        r = self.ask("Who is the caretaker of the Faro del Sur?")            # never Rafa
        self.assertEqual((r.status, r.possible), ("unverified", "Rafael"))

    def test_rejected_and_outdated_are_never_used(self):
        r = self.ask("What is the room ID of the Glass Pier?")
        self.assertEqual(r.answer, "300200")
        self.assertNotIn(self.ids["old_id"], self.used(r))
        self.assertNotIn(self.ids["wrong_care"], self.used(self.ask("Who maintains the Glass Pier?")))

    def test_same_source_never_connects(self):
        for q in ("Who maintains the Sun Room?", "What is the room ID of the room Pilar maintains?"):
            with self.subTest(q=q):
                self.assertFalse(self.ask(q).answered)

    def test_questions_outside_the_grammar_are_not_reasoned(self):
        for q in ("¿Quién es Leo?", "Tell me something nice", "Who maintains the room that is big?"):
            with self.subTest(q=q):
                r = self.ask(q)
                self.assertEqual((r.status, r.answered), ("not_reasoned", False))

    # -- privacy and audience ------------------------------------------------------
    def test_never_another_users_private_claim(self):
        r = self.ask("Who maintains the room the west door of the Old Square leads to?")
        self.assertEqual(r.status, "missing")                           # Luis's link is his
        r = self.ask("Who maintains the room the west door of the Old Square leads to?", "luis")
        self.assertEqual((r.status, r.answer, r.audience), ("established", "Luis", "personal"))
        # Luis cannot use Ana's verified chain; Ana never sees Luis's 999999 and vice versa
        self.assertEqual(self.ask("Who maintains the room the north exit of the Welcome Hall "
                                  "leads to?", "luis").status, "missing")
        r = self.ask("What is the room ID of Crystal Courtyard?")
        self.assertEqual(r.answer, "300100")
        self.assertNotIn(self.ids["luis_court"], self.used(r))
        r = self.ask("What is the room ID of Crystal Courtyard?", "luis")
        self.assertEqual((r.answer, r.audience), ("999999", "personal"))
        self.assertNotIn(self.ids["court_id"], self.used(r))
        # nothing of anyone else appears anywhere in the answer
        for who, secret in (("ana", "Luis Den"), ("ana", "999999"), ("luis", "300100"),
                            ("luis", "Mira")):
            for q in ("Who maintains the room the west door of the Old Square leads to?",
                      "What is the room ID of Crystal Courtyard?",
                      "Who maintains the room the north exit of the Welcome Hall leads to?"):
                r = self.ask(q, who)
                blob = repr((r.answer, r.possible, r.steps, r.trace))
                self.assertNotIn(secret, blob, (who, q))

    def test_proposals_are_not_used_even_by_moderators(self):
        for who in ("ana", "mod", "luis"):
            self.assertEqual(self.ask("Who maintains the Sun Room?", who).status, "missing")

    def test_shared_answer_only_from_shared_claims(self):
        for who in ("ana", "luis", "marta", "mod"):
            r = self.ask("Who maintains the room the north door of the Town Hall leads to?", who)
            self.assertEqual((r.status, r.answer, r.audience), ("established", "Olga", "shared"))
        # Ana's private gate + shared caretaker: hers only, never "shared"
        r = self.ask("Who maintains the room the east gate of the Glass Pier leads to?")
        self.assertEqual((r.status, r.answer, r.audience), ("established", "Olga", "personal"))
        r = self.ask("Who maintains the room the east gate of the Glass Pier leads to?", "luis")
        self.assertEqual(r.status, "missing")

    # -- follows changes ---------------------------------------------------------------
    def test_answers_follow_corrections_and_reviews(self):
        b, ids, u, d = _build()
        try:
            q = "Who maintains the room the north exit of the Welcome Hall leads to?"
            self.assertEqual(b.ask(q).answer, "Mira")
            b.correct(ids["mira"], "Nadia maintains Crystal Courtyard.")
            r = b.ask(q)
            self.assertEqual((r.status, r.possible), ("unverified", "Nadia"))  # needs a new review
            s = b.add_source("screenshot", title="n", reference="s://nadia")
            b.add_evidence(ids["mira"], "direct", s)
            b.review(ids["mira"], "verified")
            self.assertEqual(b.ask(q).answer, "Nadia")
            b.review(ids["hall_north"], "outdated")
            self.assertEqual(b.ask(q).status, "missing")
        finally:
            b.close()
            shutil.rmtree(d, ignore_errors=True)


class SearchChangesTests(unittest.TestCase):
    """The B2 search changes B3 makes: documented absence, the synonym gaps,
    Spanish yes/no questions."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.b = Brain(os.path.join(self.dir, "k.db"), "", "")
        self.addCleanup(self.b.close)

    def answers(self, q):
        return [h.text for h in self.b.search(q) if h.detail["why"]["matches_question"]]

    def test_absence_answers_only_about_the_named_place(self):
        self.b.learn("The Sala Azul has no caretaker.", "user", "Sala Azul")
        self.b.learn("Marta is the caretaker of the Jardín.", "user", "Jardín")
        self.b.learn("The Sala Azul is not open.", "user", "Sala Azul")
        self.assertEqual(self.answers("Who is the caretaker of the Sala Azul?"),
                         ["The Sala Azul has no caretaker."])
        self.assertNotIn("The Sala Azul has no caretaker.", self.answers("Who is the caretaker?"))
        self.assertEqual(self.answers("Which rooms are open?"), [])       # negation is not absence
        self.assertEqual(absence_terms("The Sala Azul is not open."), frozenset())

    def test_synonym_gaps(self):
        self.assertEqual(terms("Who looks after the Jardín?"), ["maintain", "jardin"])
        self.assertEqual(terms("look at the map"), ["look", "map"])       # 'look' alone is not
        self.b.learn("Tomás maintains the Biblioteca Antigua.", "user", "Biblioteca Antigua")
        self.b.learn("Pets are not allowed in the Biblioteca Antigua.", "user", "Biblioteca Antigua")
        self.b.learn("La misión del Faro da 50 monedas.", "user", "Faro")
        self.assertEqual(self.answers("Who looks after the Biblioteca Antigua?"),
                         ["Tomás maintains the Biblioteca Antigua."])
        self.assertEqual(self.answers("¿Se permiten mascotas en la Biblioteca Antigua?"),
                         ["Pets are not allowed in the Biblioteca Antigua."])
        self.assertTrue(any("monedas" in h.text for h in self.b.search("coins quest Faro")))

    def test_synonyms_stay_narrow(self):
        self.b.learn("Trading is allowed in every market.", "user", "trading")
        self.b.learn("Animals live in the Old Barn.", "user", "Old Barn")
        self.assertEqual(self.answers("¿Se permiten mascotas en la Biblioteca Antigua?"), [])
        self.assertFalse(any("Animals" in h.text for h in self.b.search("mascotas")))


class RealisticSweepTests(unittest.TestCase):
    """ask() on every Villa Brisa and Ciudad Coral question, by its own asker."""

    def sweep(self, mod, questions):
        d = tempfile.mkdtemp()
        b, ids, users = mod.build(os.path.join(d, "s.db"))
        key_of = {v: k for k, v in ids.items()}
        out = {}
        try:
            for q, kind, expect, asker in questions:
                r = b.ask(q, viewer=users[asker])
                used = [key_of[c["id"]] for st in r.steps for c in st["claims"]]
                with self.subTest(q=q, asker=asker):
                    # never another user's private claim
                    self.assertEqual([k for k in used if mod.CLAIMS[k][3] != asker
                                      and mod.CLAIMS[k][2] != "shared"], [])
                    if kind in ("unknown", "conflict", "ambiguous", "privacy"):
                        self.assertFalse(r.answered)                 # never an answer
                    if r.answered and kind == "answer":
                        self.assertTrue(set(used) & set(expect), used)
                    if r.answered:                                   # only verified claims
                        self.assertTrue(all(mod.CLAIMS[k][2] in ("verified", "shared")
                                            for k in used), used)
                out[(q, asker)] = r
        finally:
            b.close()
            shutil.rmtree(d, ignore_errors=True)
        return out

    def test_villa_brisa(self):
        import brain_eval as E1
        r = self.sweep(E1, E1.QUESTIONS + E1.HELDOUT)
        x = r[("Who is the caretaker of the Sala Azul?", "ana")]
        self.assertEqual((x.status, x.answered), ("absent", True))
        x = r[("What is the room ID of the Plaza Mayor?", "ana")]
        self.assertEqual(x.answer, "100201")                          # never the outdated 100200
        self.assertEqual(r[("Who is the caretaker of the Jardín de la Luna?", "ana")].status,
                         "missing")                                   # only a rejected claim

    def test_ciudad_coral(self):
        import brain_eval_city as E2
        r = self.sweep(E2, E2.QUESTIONS)
        multi = {q: x for (q, _), x in r.items()
                 if q in [t for t, k, _, _ in E2.QUESTIONS if k == "multi"]}
        self.assertEqual(
            {q: (x.status, x.answer or x.possible) for q, x in multi.items()},
            {"Who is the caretaker of the room the north exit of the Puerto Coral leads to?":
                 ("established", "Rafa"),
             "What is the room ID of the room where Don Tito is the encargado?":
                 ("established", "2104560"),
             "How many coins does the quest give in the room Rafael looks after?":
                 ("unverified", "50 coins")})                         # Rafael's claim unverified
        self.assertEqual(r[("What is the room ID of the Torre del Reloj?", "ana")].status, "missing")


class B2UnchangedTests(unittest.TestCase):
    """Every realistic B2 search result is unchanged by B3, except these
    intended improvements (each tested above or in test_brain_eval)."""
    INTENDED = {"What is the room ID of the Cementerio Viejo?",       # documented absence
                "Who is the caretaker of the Sala Azul?",             # documented absence
                "Who looks after the Jardín de las Rosas?",           # "looks after"
                "¿Se permiten mascotas en la Biblioteca Antigua?",    # mascotas / permitir / yes-no
                "How many coins does the quest give in the room Rafael looks after?"}  # coins

    def test_b2_results_unchanged(self):
        import b2_reference
        with open(os.path.join(os.path.dirname(__file__), "data", "b2_search_reference.json"),
                  encoding="utf-8") as fh:
            ref = json.load(fh)
        now = b2_reference.fingerprints()
        self.assertEqual(set(ref), set(now))
        changed = {k.split("|", 3)[3] for k in ref if ref[k] != now[k]}
        self.assertEqual(changed - self.INTENDED, set())
        self.assertEqual(len([k for k in ref if ref[k] != now[k]]), 10)   # 5 questions x 2 paths


if __name__ == "__main__":
    unittest.main()
