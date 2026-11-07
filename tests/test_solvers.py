"""Stage 3: built-in solvers (no AI). Reasoning is tested separately from
screen reading: these questions are typed in, except the last class which
solves the real recorded question screens."""
import os
import sys
import tempfile
import unittest
from fractions import Fraction

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import ORDER, Answer, Question  # noqa: E402
from puzzle_assistant.qa.solvers import evaluate, solve_question  # noqa: E402
from puzzle_assistant.qa.text import normalize  # noqa: E402


def ask(text, options, category="PREGUNTA", clues=None, kind="choice"):
    return solve_question(Question(category, text, options, kind, clues or []))


class EvaluateTests(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(evaluate("63 − 8"), 55)
        self.assertEqual(evaluate("(2 + 3) × 4"), 20)
        self.assertEqual(evaluate("2 + 3 × 4"), 14)
        self.assertEqual(evaluate("10 ÷ 4"), Fraction(5, 2))
        self.assertEqual(evaluate("1,5 + 1"), Fraction(5, 2))
        for bad in ("abc", "8 ÷ 0", "2 +", "Pulpo", ""):
            self.assertIsNone(evaluate(bad), bad)


class MandatoryExamples(unittest.TestCase):
    """The two examples from the development manual."""

    def test_sequence_example(self):
        a = ask("¿Qué letra sigue? V, W, X, Y", ["Z", "X", "W", "V"])
        self.assertEqual((a.choice, a.confidence), ("Z", 1.0))
        self.assertIn("+1", a.explanation)

    def test_error_example_needs_knowledge(self):
        # facts about authors are not something the built-in solvers know:
        # they must NOT guess (this one is for memory / AI)
        self.assertIsNone(ask("¿Qué frase tiene un error?", [
            "Cervantes escribió en español.", "Shakespeare escribió en francés.",
            "La Odisea se compuso en griego antiguo.", "Dante escribió la Divina Comedia en italiano."]))


class SequenceTests(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(ask("¿Qué letra sigue?", ["U", "X", "W", "V"],
                             clues=["F, J, N, R, ?"]).choice, "V")          # from the game
        self.assertEqual(ask("¿Qué letra sigue? Z, X, V", ["U", "T", "S", "W"]).choice, "T")
        self.assertEqual(ask("¿Qué letra va antes? D, F, H", ["A", "B", "C", "E"]).choice, "B")

    def test_numbers(self):
        cases = [("¿Qué número sigue? 3, 6, 9, 12", ["14", "15", "16", "18"], "15"),
                 ("¿Qué número sigue? 2, 4, 8, 16", ["24", "30", "32", "64"], "32"),
                 ("¿Qué número sigue? 1, 1, 2, 3, 5", ["7", "8", "9", "10"], "8"),
                 ("¿Qué número sigue? 1, 4, 9, 16", ["20", "25", "24", "36"], "25"),
                 ("¿Qué número sigue? 2, 3, 5, 8, 12", ["15", "16", "17", "18"], "17"),
                 ("¿Qué número sigue? 1, 3, 2, 4, 3", ["4", "5", "6", "2"], "5"),
                 ("¿Qué número falta? 2, 4, ?, 8", ["5", "6", "7", "3"], "6"),
                 ("¿Qué número sigue? 20, 17, 14", ["10", "11", "12", "13"], "11"),
                 ("What number comes next? 5, 10, 15", ["20", "25", "30", "16"], "20")]
        for text, opts, want in cases:
            with self.subTest(text):
                a = ask(text, opts)
                self.assertIsNotNone(a)
                self.assertEqual(a.choice, want)

    def test_refuses_when_unsure(self):
        # 1, 2, 4 -> 8 (x2) or 7 (+1 +2 +3): both offered -> no answer
        self.assertIsNone(ask("¿Qué número sigue? 1, 2, 4", ["7", "8", "5"]))
        self.assertEqual(ask("¿Qué número sigue? 1, 2, 4", ["8", "9", "5"]).choice, "8")
        self.assertIsNone(ask("¿Qué número sigue? 2, 4", ["6", "8"]))          # too short
        self.assertIsNone(ask("¿Qué número sigue? 3, 1, 4, 1, 5", ["9", "2", "6"]))  # no simple rule
        self.assertIsNone(ask("¿Qué número sigue? 3, 6, 9", ["13", "14"]))     # answer not offered


class OrderTests(unittest.TestCase):
    def test_sums_from_the_game(self):
        a = ask("Ordena las cuentas de menor a mayor resultado", ["9 + 12", "21 + 10", "63 − 8", "36 + 10"],
                "ORDENAR", kind=ORDER)
        self.assertEqual(a.order, ["9 + 12", "21 + 10", "36 + 10", "63 − 8"])
        self.assertIn("63 − 8 = 55", a.explanation)

    def test_stray_marks_around_sums(self):
        # a stray symbol in front / letters behind are ignored, digits never are
        a = ask("Ordena las cuentas de menor a mayor resultado",
                ["9+12", "21+10", "63 - 8", "> 36 +10 Jj"], "ORDENAR", kind=ORDER)
        self.assertEqual(a.order[2], "> 36 +10 Jj")
        self.assertIsNone(ask("Ordena las cuentas de menor a mayor resultado",
                              ["9+12", "21+10", "63 - 8 1", "36 + 10"], "ORDENAR", kind=ORDER))

    def test_descending_and_operators(self):
        a = ask("Ordena de mayor a menor", ["3×4", "20-9", "2+2", "5"], "ORDENAR", kind=ORDER)
        self.assertEqual(a.order, ["3×4", "20-9", "5", "2+2"])

    def test_refuses(self):
        # game knowledge (fish prices), unknown direction, and ties: no answer
        self.assertIsNone(ask("Ordena los peces de menor a mayor precio de referencia",
                              ["Pulpo", "Sardina", "Atún", "Pez espada", "Dorada"], "ORDENAR", kind=ORDER))
        self.assertIsNone(ask("Ordena las cuentas", ["1+1", "3", "4"], "ORDENAR", kind=ORDER))
        self.assertIsNone(ask("Ordena de menor a mayor", ["1+1", "2", "4"], "ORDENAR", kind=ORDER))


class CalculationTests(unittest.TestCase):
    def test_calculation(self):
        self.assertEqual(ask("¿Cuánto es 7 × 8?", ["54", "56", "64", "48"]).choice, "56")
        self.assertEqual(ask("¿Cuánto es (2 + 3) × 4?", ["14", "20", "24"]).choice, "20")

    def test_wrong_and_right_equation(self):
        opts = ["2 + 2 = 4", "3 × 3 = 9", "10 − 4 = 5", "8 ÷ 2 = 4"]
        a = ask("¿Qué frase tiene un error?", opts, "ENCUENTRA EL ERROR")
        self.assertEqual(a.choice, "10 − 4 = 5")
        self.assertEqual(a.explanation, "10 − 4 = 6, no 5")
        opts = ["2 + 2 = 5", "3 × 3 = 9", "10 − 4 = 5"]
        self.assertEqual(ask("¿Cuál es correcta?", opts).choice, "3 × 3 = 9")
        self.assertIsNone(ask("¿Qué frase tiene un error?", ["2 + 2 = 5", "1 + 1 = 3"]))  # two wrong


class WordProblemTests(unittest.TestCase):
    def test_from_the_game_is_only_likely(self):
        a = ask("Estudias 45 minutos y luego 25 minutos más. ¿Cuántos minutos estudiaste?",
                ["79", "69", "70", "71"], "ADIVINANZA")
        self.assertEqual(a.choice, "70")
        self.assertLess(a.confidence, 1.0)      # shown as "Probably", never certain

    def test_refuses_unclear_wording(self):
        self.assertIsNone(ask("Tienes 10 monedas, ganas 5 y gastas 3. ¿Cuántas te quedan?",
                              ["12", "8", "18"]))     # mixed operations
        self.assertIsNone(ask("Compras el doble de 6 manzanas. ¿Cuántas tienes?", ["12", "3", "8"]))


class SolverMemoryTests(unittest.TestCase):
    def test_solver_score(self):
        m = Memory(os.path.join(tempfile.mkdtemp(), "m.db"))
        try:
            q = Question("PREGUNTA", "¿Qué letra sigue?", ["U", "X", "W", "V"], clues=["F, J, N, R, ?"])
            mine = solve_question(q)
            m.save(q, Answer(choice="V", source="game", verified=True))
            m.log_solver(q, mine, normalize(mine.summary()) == "v")
            self.assertEqual((m.stats()["solver_right"], m.stats()["solver_wrong"]), (1, 0))
            # the game's answer has priority over a solver answer
            self.assertEqual(m.save(q, Answer(choice="U", source="solver")), "blocked")
        finally:
            m.close()


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class RealScreensTests(unittest.TestCase):
    """Read the recorded question screens, then solve them with no memory."""

    def solve_fixture(self, name):
        from puzzle_assistant.ocr import Tesseract
        from puzzle_assistant.qa.screen import locate_question, read_question
        img = cv2.imread(os.path.join(HERE, "fixtures", "questions", name + ".png"))
        return read_question(img, locate_question(img), Tesseract(Config())), solve_question

    def test_letters_screen(self):
        s, solve = self.solve_fixture("letras_open")
        self.assertEqual(solve(s.question).choice, "V")

    def test_sums_screen(self):
        s, solve = self.solve_fixture("ordenar_sumas_answered")
        a = solve(s.question)
        self.assertEqual([normalize(x) for x in a.order], ["9+12", "21+10", "36+10", "63-8"])
        # and it agrees with what the game revealed
        self.assertEqual([normalize(x) for x in s.revealed.order], [normalize(x) for x in a.order])

    def test_word_problem_screen(self):
        s, solve = self.solve_fixture("pista_answered")
        a = solve(s.question)
        self.assertEqual(a.choice, "70")
        self.assertEqual(a.choice, s.revealed.choice)

    def test_knowledge_screens_are_left_alone(self):
        for name in ("adivinanza_open", "ordenar_peces_open", "parejas_animales_open",
                     "error_answered", "quien_soy_answered"):
            s, solve = self.solve_fixture(name)
            self.assertIsNone(solve(s.question), name)


if __name__ == "__main__":
    unittest.main()
