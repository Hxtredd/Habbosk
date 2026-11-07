"""'Preguntas express' (timed general-knowledge task) and the guard against
non-question panels (the job panel's task list was answered by the AI)."""
import os
import queue
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402

QF = os.path.join(HERE, "fixtures", "questions")


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class ExpressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from puzzle_assistant.ocr import Tesseract
        cls.ocr = Tesseract(Config())

    def test_reads_the_express_window(self):
        from puzzle_assistant.qa.screen import EXPRESS, locate_express, locate_question, read_question
        img = cv2.imread(os.path.join(QF, "express_open.png"))
        self.assertIsNone(locate_question(img))           # no yellow label: other detector
        lay = locate_express(img, self.ocr)
        self.assertIsNotNone(lay)
        s = read_question(img, lay, self.ocr)
        self.assertEqual(s.question.category, EXPRESS)
        self.assertEqual(s.question.text, "¿Qué océano está entre África y Australia?")
        self.assertEqual(s.question.options, ["El Atlántico", "El Ártico", "El Pacífico", "El Índico"])
        self.assertFalse(s.answered)

    def test_express_is_not_found_in_library_questions(self):
        from puzzle_assistant.qa.screen import locate_express
        for name in ("adivinanza_open", "ordenar_sumas_answered", "parejas_animales_open"):
            self.assertIsNone(locate_express(cv2.imread(os.path.join(QF, name + ".png")), self.ocr), name)

    def run_engine(self, img, ticks=6, results=None):
        from puzzle_assistant import engine as E
        from puzzle_assistant.analyzer import Analyzer
        from puzzle_assistant.qa.screen import locate_express, locate_question
        cfg = Config()
        eng = E.Engine(cfg, queue.Queue())
        an, st = Analyzer(cfg, self.ocr), {}
        lay = locate_question(img) or locate_express(img, self.ocr)
        for _ in range(ticks):
            r = eng._question_tick(({"left": 0, "top": 0}, img, lay), an, False, st)
            if results is not None:
                results.append(r)
        out = []
        while not eng.out.empty():
            out.append(eng.out.get())
        eng.stop()
        return out

    def test_engine_handles_express(self):
        msgs = self.run_engine(cv2.imread(os.path.join(QF, "express_open.png")))
        self.assertEqual([m["status"] for m in msgs], ["unknown"])     # no memory, no AI here
        self.assertEqual(msgs[0]["question"].options[3], "El Índico")

    def test_panel_without_a_quiz_label_is_ignored(self):
        # a column of boxes under some yellow text that is not a quiz category
        res = []
        msgs = self.run_engine(cv2.imread(os.path.join(QF, "no_label_panel.png")), ticks=10,
                               results=res)
        self.assertEqual(msgs, [])
        # it is then reported as "no question here", so the engine goes back
        # to "waiting" and the marks of a finished puzzle are cleared
        self.assertEqual(res[-1], False)
        res = []
        self.run_engine(cv2.imread(os.path.join(QF, "express_open.png")), results=res)
        self.assertTrue(all(res))


if __name__ == "__main__":
    unittest.main()
