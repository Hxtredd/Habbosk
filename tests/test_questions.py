"""Stage 2: reading the game's question window (tests/fixtures/questions,
crops of the window from recorded library rounds).

Reading the screen is tested separately from answering it: these tests only
check what was READ (category, question, clues, options, the result the game
revealed, the explanation). The recordings include the mouse pointer, which
sometimes adds a stray fragment to an option ('Sardina Jj'); live screen
captures do not contain the pointer, so options are compared with that
tolerance.
"""
import os
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.qa.memory import Memory, _strip_tail  # noqa: E402
from puzzle_assistant.qa.screen import locate_question, read_question, split_question  # noqa: E402
from puzzle_assistant.qa.text import normalize  # noqa: E402

RASPADITO = ["Una mesa de UNO", "El Casino de la Playa", "El raspadito", "La tragamonedas de palanca"]
CASES = {
    "adivinanza_open": dict(cat="ADIVINANZA", kind="choice", answered=False, options=RASPADITO,
                            text="Soy un boleto instantáneo que se compra en cualquier lugar con "
                                 ":raspadito. ¿Qué soy?"),
    "adivinanza_answered": dict(cat="ADIVINANZA", kind="choice", answered=True, options=RASPADITO,
                                answer="El raspadito",
                                expl="El resultado se revela a los pocos segundos"),
    "letras_open": dict(cat="PREGUNTA", kind="choice", answered=False, text="¿Qué letra sigue?",
                        clues=["F, J, N, R, ?"], options=["U", "X", "W", "V"]),
    "letras_answered": dict(cat="PREGUNTA", kind="choice", answered=True, answer="V",
                            options=["U", "X", "W", "V"],
                            expl="Cada letra salta +4 en el alfabeto: sigue la V."),
    "ordenar_sumas_answered": dict(cat="ORDENAR", kind="order", answered=True,
                                   options=["9+12", "21+10", "63-8", "36+10"],
                                   order=["9+12", "21+10", "36+10", "63-8"]),
    "ordenar_peces_open": dict(cat="ORDENAR", kind="order", answered=False,
                               options=["Pulpo", "Sardina", "Atún", "Pez espada", "Dorada"]),
    "ordenar_peces_answered": dict(cat="ORDENAR", kind="order", answered=True,
                                   order=["Sardina", "Dorada", "Pulpo", "Atún", "Pez espada"]),
    "parejas_animales_open": dict(cat="PAREJAS", kind="pairs", answered=False,
                                  text="Une cada animal con su alimento favorito",
                                  options=["Panda", "Koala", "Vaca", "Abeja"],
                                  right=["Pasto", "Néctar", "Eucalipto", "Bambú"]),
    "parejas_animales_answered": dict(cat="PAREJAS", kind="pairs", answered=True,
                                      pairs={"Panda": "Bambú", "Koala": "Eucalipto",
                                             "Vaca": "Pasto", "Abeja": "Néctar"}),
    "parejas_gimnasio_answered": dict(cat="PAREJAS", kind="pairs", answered=True,
                                      pairs={"Cinta de correr": "Saltar vallas y esquivar conos",
                                             "Elíptica": "Cadencia dentro de la franja",
                                             "Banco de press": "Empujar la barra hasta arriba",
                                             "Sentadilla": "Equilibrar la barra"}),
    "error_answered": dict(cat="ENCUENTRA EL ERROR", kind="choice", answered=True,
                           text="¿Qué frase tiene un error?",
                           answer="Con :dar puedes entregar dinero que tienes en el banco."),
    "quien_soy_answered": dict(cat="¿QUIÉN SOY?", kind="choice", answered=True,
                               text="Soy un asistente NPC", answer="Leo",
                               options=["Rubén", "Mateo", "Tomás", "Leo"]),
    "pista_open": dict(cat="ADIVINANZA", kind="choice", answered=False,
                       text="Estudias 45 minutos y luego 25 minutos más. ¿Cuántos minutos estudiaste?",
                       clues=["Son dos ratos seguidos"]),
    "pista_answered": dict(cat="ADIVINANZA", kind="choice", answered=True, answer="70",
                           options=["79", "69", "70", "71"], expl="La cuenta da 70"),
}


def same(a: str, b: str) -> bool:
    """Equal after normalising; a long text may differ by one reading slip."""
    import difflib
    na, nb = normalize(a), normalize(b)
    if na == nb or _strip_tail(na) == _strip_tail(nb):
        return True
    return len(nb) >= 12 and difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9


class SplitTests(unittest.TestCase):
    def test_split_question(self):
        self.assertEqual(split_question(["¿Qué letra sigue? V, W, X, Y"]),
                         ("¿Qué letra sigue?", ["V, W, X, Y"]))
        self.assertEqual(split_question(["Estudias 45 minutos. ¿Cuántos?", "• Son dos ratos seguidos."]),
                         ("Estudias 45 minutos. ¿Cuántos?", ["Son dos ratos seguidos"]))
        self.assertEqual(split_question(["1. Soy un asistente NPC."]), ("Soy un asistente NPC", []))


class OtherPuzzlesIgnoreQuestionsTests(unittest.TestCase):
    def test_not_mistaken_for_puzzles(self):
        """Question windows (e.g. the 2-column PAREJAS grid) must not be taken
        for a weapon or money puzzle - and checking must stay fast."""
        import time
        from puzzle_assistant.locate import search
        from puzzle_assistant.money import locate_money
        from puzzle_assistant.vision import to_gray
        for name in CASES:
            img = cv2.imread(os.path.join(HERE, "fixtures", "questions", name + ".png"))
            t = time.perf_counter()
            self.assertIsNone(search(img, to_gray(img), Config()), name)
            self.assertIsNone(locate_money(to_gray(img)), name)
            self.assertLess(time.perf_counter() - t, 1.0, name)

    def test_wide_component_is_fast(self):
        """Regression: a long run of touching glyphs once took exponential time."""
        import time
        import numpy as np
        from puzzle_assistant.glyphs import read_component
        ink = np.zeros((10, 400), np.uint8)
        ink[2:9, :] = 200
        t = time.perf_counter()
        read_component(ink, (0, 2, 400, 7), "0123456789", 5.6)
        read_component(ink, (0, 2, 33, 7), "0123456789", 5.6)
        self.assertLess(time.perf_counter() - t, 2.0)


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class ReadQuestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from concurrent.futures import ThreadPoolExecutor
        from puzzle_assistant.ocr import Tesseract
        cls.ocr = Tesseract(Config())
        cls.pool = ThreadPoolExecutor(8)
        cls.screens = {}
        for name in CASES:
            img = cv2.imread(os.path.join(HERE, "fixtures", "questions", name + ".png"))
            lay = locate_question(img)
            cls.screens[name] = read_question(img, lay, cls.ocr, cls.pool) if lay else None

    def test_each_screen(self):
        for name, want in CASES.items():
            with self.subTest(name):
                s = self.screens[name]
                self.assertIsNotNone(s, "question window not found")
                q = s.question
                self.assertEqual(q.category, want["cat"])
                self.assertEqual(q.kind, want["kind"])
                self.assertEqual(s.answered, want["answered"])
                if "text" in want:
                    self.assertTrue(same(q.text, want["text"]), q.text)
                if "clues" in want:
                    self.assertEqual(len(q.clues), len(want["clues"]), q.clues)
                    for got, exp in zip(q.clues, want["clues"]):
                        self.assertTrue(same(got, exp), (got, exp))
                if "options" in want:
                    self.assertEqual(len(q.options), len(want["options"]), q.options)
                    for got, exp in zip(q.options, want["options"]):
                        self.assertTrue(same(got, exp), (got, exp))
                if "right" in want:
                    for got, exp in zip(q.right, want["right"]):
                        self.assertTrue(same(got, exp), (got, exp))
                r = s.revealed
                if "answer" in want:
                    self.assertTrue(r is not None and same(r.choice, want["answer"]), r)
                if "order" in want:
                    self.assertIsNotNone(r)
                    self.assertEqual([normalize(x) for x in r.order],
                                     [normalize(x) for x in want["order"]])
                if "pairs" in want:
                    self.assertIsNotNone(r)
                    got = {_strip_tail(normalize(a)): normalize(b) for a, b in r.pairs.items()}
                    self.assertEqual(got, {normalize(a): normalize(b) for a, b in want["pairs"].items()})
                if "expl" in want:
                    self.assertTrue(normalize(s.explanation).startswith(normalize(want["expl"])),
                                    s.explanation)
                if not want["answered"]:
                    self.assertIsNone(r)

    def test_learn_then_answer_from_memory(self):
        """What the game reveals is learned and answers the next appearance."""
        import tempfile
        m = Memory(os.path.join(tempfile.mkdtemp(), "m.db"))
        try:
            for name, s in self.screens.items():
                if s and s.answered and s.revealed:
                    m.save(s.question, s.revealed)
            for name in ("adivinanza_open", "letras_open", "ordenar_peces_open",
                         "parejas_animales_open"):
                hit = m.lookup(self.screens[name].question)
                self.assertIsNotNone(hit, name)
            self.assertEqual(m.lookup(self.screens["letras_open"].question).answer.choice, "V")
            self.assertEqual(m.lookup(self.screens["adivinanza_open"].question).answer.choice,
                             "El raspadito")
            order = m.lookup(self.screens["ordenar_peces_open"].question).answer.order
            self.assertEqual([_strip_tail(normalize(x)) for x in order],
                             ["sardina", "dorada", "pulpo", "atun", "pez espada"])
        finally:
            m.close()


if __name__ == "__main__":
    unittest.main()
