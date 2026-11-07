"""AI answers drawn on the question window (green = sure, yellow = not sure)
and ORDENAR progress: the highlight follows 'Toca el paso N'."""
import os
import queue
import sys
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.overlay import question_overlay_items  # noqa: E402
from puzzle_assistant.qa.model import ORDER, PAIRS, Answer, Question  # noqa: E402

BOXES = [(0, 40 * i, 300, 36) for i in range(8)]
PLACE = {"boxes": BOXES[:4], "n_left": 0}
Q = Question("PREGUNTA", "¿Capital de Francia?", ["Madrid", "París", "Roma", "Lima"])
O = Question("ORDENAR", "Ordena", ["a", "b", "c", "d"], ORDER)
ORD = Answer(ORDER, order=["c", "a", "d", "b"])


def kinds(items):
    return [(i.kind, i.text) for i in sorted(items, key=lambda i: i.y)]


class MarkTests(unittest.TestCase):
    def test_ai_choice_green_when_sure_yellow_when_not(self):
        a = Answer(choice="París", source="ai", confidence=0.9)
        self.assertEqual(kinds(question_overlay_items(Q, a, "ai", PLACE)), [("next", "AI")])
        self.assertEqual(kinds(question_overlay_items(Q, a, "unsure", PLACE)), [("doubt", "AI")])
        self.assertEqual(kinds(question_overlay_items(Q, a, "memory", PLACE)), [("answer", "")])
        self.assertEqual(kinds(question_overlay_items(Q, a, "likely", PLACE)), [("doubt", "?")])
        for st in ("unknown", "thinking", "game", "answered"):
            self.assertEqual(question_overlay_items(Q, a, st, PLACE), [], st)
        self.assertEqual(question_overlay_items(Q, a, "ai", PLACE)[0].y, 40 + 18)   # on 'París'

    def test_order_highlight_moves_with_progress(self):
        # options a b c d; order c a d b -> a=2, b=4, c=1, d=3
        self.assertEqual(kinds(question_overlay_items(O, ORD, "solver", PLACE)),
                         [("step", "2"), ("step", "4"), ("next", "1"), ("step", "3")])
        self.assertEqual(kinds(question_overlay_items(O, ORD, "solver", PLACE, progress=2)),
                         [("done", ""), ("step", "4"), ("done", ""), ("next", "3")])
        self.assertEqual(kinds(question_overlay_items(O, ORD, "unsure", PLACE, progress=1)),
                         [("doubt", "2"), ("step", "4"), ("done", ""), ("step", "3")])

    def test_pairs_colour(self):
        p = Question("PAREJAS", "Une", ["x", "y"], PAIRS, right=["1", "2"])
        a = Answer(PAIRS, pairs={"x": "2", "y": "1"})
        place = {"boxes": BOXES[:4], "n_left": 2}
        self.assertTrue(all(i.kind == "doubt" for i in question_overlay_items(p, a, "unsure", place)))
        self.assertTrue(all(i.kind == "step" for i in question_overlay_items(p, a, "ai", place)))


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class ProgressTests(unittest.TestCase):
    def test_step_line_is_read_and_engine_emits_progress(self):
        from puzzle_assistant import engine as E
        from puzzle_assistant.analyzer import Analyzer
        from puzzle_assistant.ocr import Tesseract
        from puzzle_assistant.qa.screen import fingerprint, locate_question, read_question
        cfg = Config()
        ocr = Tesseract(cfg)
        one = cv2.imread(os.path.join(HERE, "fixtures", "questions", "ordenar_division_open.png"))
        two = cv2.imread(os.path.join(HERE, "fixtures", "questions",
                                      "ordenar_division_paso2_synthetic.png"))
        l1, l2 = locate_question(one), locate_question(two)
        self.assertEqual(read_question(one, l1, ocr).progress, 0)
        self.assertEqual(read_question(two, l2, ocr).progress, 1)
        self.assertNotEqual(fingerprint(one, l1), fingerprint(two, l2))   # tap is noticed
        eng = E.Engine(cfg, queue.Queue())
        an, st, b = Analyzer(cfg, ocr), {}, {"left": 0, "top": 0}
        for _ in range(3):
            eng._question_tick((b, one, l1), an, False, st)
        for _ in range(3):
            eng._question_tick((b, two, l2), an, False, st)
        msgs = []
        while not eng.out.empty():
            msgs.append(eng.out.get())
        eng.stop()
        self.assertEqual([m["state"] for m in msgs], ["question", "question_progress"])
        self.assertEqual((msgs[0]["status"], msgs[0]["progress"]), ("solver", 0))
        self.assertEqual(msgs[1]["progress"], 1)


if __name__ == "__main__":
    unittest.main()
