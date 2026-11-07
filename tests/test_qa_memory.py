"""Stage 1: question memory, text helpers and key storage.

Uses real questions from the recorded library rounds and the two mandatory
examples from the development manual.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from puzzle_assistant import secrets  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import ORDER, PAIRS, Answer, Question  # noqa: E402
from puzzle_assistant.qa.text import guess_language, normalize  # noqa: E402

ERROR_Q = Question("PREGUNTA", "¿Qué frase tiene un error?", [
    "Cervantes escribió en español.", "Shakespeare escribió en francés.",
    "La Odisea se compuso en griego antiguo.", "Dante escribió la Divina Comedia en italiano."])
SEQ_Q = Question("PREGUNTA", "¿Qué letra sigue? V, W, X, Y", ["Z", "X", "W", "V"],
                 clues=["V, W, X, Y"])
TROYA = Question("ADIVINANZA", "Parezco un regalo, pero escondía soldados griegos para "
                 "conquistar una ciudad. ¿Qué soy?",
                 ["La espada de Aquiles", "El escudo de Esparta", "El barco de Ulises",
                  "El caballo de Troya"])
FISH = Question("ORDENAR", "Ordena los peces de menor a mayor precio de referencia",
                ["Pulpo", "Sardina", "Atún", "Pez espada", "Dorada"], kind=ORDER)
ANIMALS = Question("PAREJAS", "Une cada animal con su alimento favorito",
                   ["Panda", "Koala", "Vaca", "Abeja"], kind=PAIRS,
                   right=["Pasto", "Néctar", "Eucalipto", "Bambú"])


def shuffled(q: Question, options, right=None) -> Question:
    return Question(q.category, q.text, list(options), q.kind, list(q.clues),
                    list(right if right is not None else q.right))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "m.db")
        self.m = Memory(self.path)

    def tearDown(self):
        self.m.close()

    def test_exact_and_reordered(self):
        self.m.save(ERROR_Q, Answer(choice="Shakespeare escribió en francés.", source="ai",
                                    explanation="Shakespeare escribió en inglés."))
        hit = self.m.lookup(ERROR_Q)
        self.assertEqual((hit.how, hit.answer.choice),
                         ("exact", "Shakespeare escribió en francés."))
        q2 = shuffled(ERROR_Q, reversed(ERROR_Q.options))
        hit = self.m.lookup(q2)
        self.assertEqual(hit.answer.choice, "Shakespeare escribió en francés.")
        self.assertEqual(hit.answer.explanation, "Shakespeare escribió en inglés.")

    def test_reworded(self):
        self.m.save(TROYA, Answer(choice="El caballo de Troya", source="game", verified=True))
        q = shuffled(TROYA, TROYA.options)
        q.text = "Parezco un regalo pero escondía soldados griegos para conquistar la ciudad. ¿Qué soy?"
        hit = self.m.lookup(q)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.answer.choice, "El caballo de Troya")

    def test_negation_is_never_reused(self):
        self.m.save(ERROR_Q, Answer(choice="Shakespeare escribió en francés.", source="game",
                                    verified=True))
        q = shuffled(ERROR_Q, ERROR_Q.options)
        q.text = "¿Qué frase es correcta?"
        self.assertIsNone(self.m.lookup(q))
        q.text = "¿Qué frase no tiene un error?"
        self.assertIsNone(self.m.lookup(q))

    def test_changed_distractors(self):
        self.m.save(SEQ_Q, Answer(choice="Z", source="solver", verified=True))
        hit = self.m.lookup(shuffled(SEQ_Q, ["A", "Z", "B", "C"]))
        self.assertEqual(hit.answer.choice, "Z")
        self.assertIsNone(self.m.lookup(shuffled(SEQ_Q, ["A", "B", "C", "D"])))

    def test_source_priority_and_corrections(self):
        self.assertEqual(self.m.save(TROYA, Answer(choice="El caballo de Troya", source="game",
                                                   verified=True)), "new")
        # an AI guess never overwrites what the game showed
        self.assertEqual(self.m.save(TROYA, Answer(choice="El barco de Ulises", source="ai")),
                         "blocked")
        self.assertEqual(self.m.lookup(TROYA).answer.choice, "El caballo de Troya")
        # an AI answer is replaced by the game's answer
        self.m.save(SEQ_Q, Answer(choice="X", source="ai"))
        self.assertEqual(self.m.save(SEQ_Q, Answer(choice="Z", source="game", verified=True)),
                         "updated")
        self.assertEqual(self.m.lookup(SEQ_Q).answer.choice, "Z")
        # an explicit user correction always applies and is logged
        self.assertEqual(self.m.correct(TROYA, Answer(choice="El barco de Ulises")), "updated")
        self.assertEqual(self.m.lookup(TROYA).answer.choice, "El barco de Ulises")
        self.assertEqual(self.m.stats()["corrections"], 1)

    def test_order_and_pairs(self):
        self.m.save(FISH, Answer(kind=ORDER, order=["Sardina", "Dorada", "Pulpo", "Atún",
                                                    "Pez espada"], source="game", verified=True))
        q = shuffled(FISH, ["Dorada", "Atún", "Pulpo", "Pez espada", "Sardina"])
        self.assertEqual(self.m.lookup(q).answer.order,
                         ["Sardina", "Dorada", "Pulpo", "Atún", "Pez espada"])
        self.m.save(ANIMALS, Answer(kind=PAIRS, pairs={"Panda": "Bambú", "Koala": "Eucalipto",
                                                       "Vaca": "Pasto", "Abeja": "Néctar"},
                                    source="game", verified=True))
        q = shuffled(ANIMALS, ["Vaca", "Abeja", "Panda", "Koala"], ["Bambú", "Pasto", "Néctar",
                                                                     "Eucalipto"])
        self.assertEqual(self.m.lookup(q).answer.pairs["Koala"], "Eucalipto")

    def test_feedback_persistence_export_clear(self):
        self.m.save(TROYA, Answer(choice="El caballo de Troya", source="game", verified=True))
        self.m.record_feedback(TROYA, "El escudo de Esparta", False)
        self.m.add_usage("gemini", 2, 0.0)
        self.m.close()
        self.m = Memory(self.path)                     # "restart"
        self.assertEqual(self.m.lookup(TROYA).answer.choice, "El caballo de Troya")
        self.assertEqual(self.m.month_usage(), (2, 0.0))
        out = os.path.join(self.dir, "export.json")
        self.assertEqual(self.m.export_json(out), 1)
        data = json.load(open(out, encoding="utf-8"))
        self.assertEqual(data["questions"][0]["times_wrong"], 1)
        self.m.clear()
        self.assertEqual(self.m.stats()["questions"], 0)


class TextTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("¿Qué letra sigue?  V, W"), "que letra sigue v w")
        self.assertEqual(normalize("63 − 8"), normalize("63 - 8"))   # game minus vs OCR dash
        self.assertEqual(normalize("63 − 8"), "63-8")
        self.assertEqual(normalize("36 +10"), normalize("36 + 10"))

    def test_language(self):
        self.assertEqual(guess_language("¿Qué letra sigue? V, W, X, Y"), "es")
        self.assertEqual(guess_language("Which city is the capital of France?"), "en")
        self.assertEqual(guess_language("Une cada animal con su alimento favorito"), "es")


class SecretsTests(unittest.TestCase):
    def test_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), "keys.bin")
        self.assertFalse(secrets.has_key("ai:gemini", path))
        secrets.set_key("ai:gemini", " abc123 ", path)
        self.assertEqual(secrets.get_key("ai:gemini", path), "abc123")
        with open(path, "rb") as fh:
            self.assertNotIn(b"abc123", fh.read())   # never stored as plain text
        secrets.set_key("ai:gemini", None, path)
        self.assertFalse(secrets.has_key("ai:gemini", path))


if __name__ == "__main__":
    unittest.main()
