"""v3.1: the weapon puzzle's marks must go once the puzzle closes, even when a
window that MIGHT be a quiz stays on screen and keeps changing (the job
panel's task list: "Montar un arma - a 2 casillas" changes as the player
walks). That window must not count as busy either (it paused the room
explorer). The engine's real loop runs on a fake screen."""
import os
import queue
import sys
import time
import types
import unittest
from unittest import mock

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant import engine as E  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402

PANEL = cv2.imread(os.path.join(HERE, "fixtures", "puzzle_8_pieces.png"))
BOUNDS = {"left": 0, "top": 0, "width": 1280, "height": 720}


def screen(with_puzzle: bool) -> np.ndarray:
    img = np.full((720, 1280, 3), 22, np.uint8)
    if with_puzzle:
        img[150:150 + PANEL.shape[0], 400:400 + PANEL.shape[1]] = PANEL
    return img


class FakeGrabber:
    """The puzzle is on screen until `closed` is set; then only the room is."""
    closed = False

    def __init__(self, monitor=0):
        self.screens = [BOUNDS]
        self.monitor = BOUNDS

    def targets(self, region):
        return [BOUNDS]

    def grab_bounds(self, b):
        return screen(not FakeGrabber.closed), BOUNDS

    def close(self):
        pass


class JobPanel:
    """A window that looks like a list of options but is not a quiz; its
    fingerprint changes every check (distances change as the player walks)."""
    def __init__(self):
        self.n = 0

    def locate(self, frame):
        if not FakeGrabber.closed:
            return None
        lay = types.SimpleNamespace(left=[], all_boxes=lambda: [(20, 400, 250, 37)] * 3)
        return lay

    def fingerprint(self, frame, lay):
        self.n += 1
        return self.n

    def read(self, frame, lay, ocr, pool):
        q = types.SimpleNamespace(category="", text="Montar un arma", kind="choice")
        return types.SimpleNamespace(question=q, answered=False, progress=0, revealed=None,
                                     verdict="")


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class MarksAfterThePuzzle(unittest.TestCase):
    def run_engine(self, with_job_panel: bool):
        FakeGrabber.closed = False
        cfg = Config()
        cfg.region = None
        cfg.money_puzzle = False
        out = queue.Queue()
        eng = E.Engine(cfg, out)
        job = JobPanel()
        patches = [mock.patch.object(E, "ScreenGrabber", FakeGrabber),
                   mock.patch.object(E, "locate_express", lambda *a, **k: None),
                   mock.patch.object(E, "locate_question",
                                     job.locate if with_job_panel else (lambda f: None)),
                   mock.patch.object(E, "fingerprint", job.fingerprint),
                   mock.patch.object(E, "read_question", job.read)]
        for p in patches:
            p.start()
        events = []
        try:
            eng.start()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:            # wait until the puzzle is shown
                m = out.get(timeout=20)
                events.append(m)
                if m.get("state") == "detected" and m.get("place"):
                    break
            self.assertTrue(any(e.get("state") == "detected" for e in events), events)
            FakeGrabber.closed = True                      # the player finishes the puzzle
            closed_at = time.monotonic()
            cleared_at = None
            busy_after = []
            while time.monotonic() - closed_at < 4.0:
                try:
                    m = out.get(timeout=0.1)
                except queue.Empty:
                    m = None
                if m is not None:
                    events.append(m)
                    if cleared_at is None and m.get("state") in ("overlay_clear", "waiting"):
                        cleared_at = time.monotonic()
                if time.monotonic() - closed_at > 2.0:
                    busy_after.append(eng.busy)
            return cleared_at, closed_at, busy_after, events
        finally:
            eng.stop()
            eng.join(5)
            for p in patches:
                p.stop()

    def test_marks_go_when_nothing_else_is_on_screen(self):
        cleared_at, closed_at, busy_after, _ = self.run_engine(False)
        self.assertIsNotNone(cleared_at)
        self.assertLess(cleared_at - closed_at, 2.0)
        self.assertFalse(any(busy_after))

    def test_marks_go_even_with_a_changing_job_panel_on_screen(self):
        cleared_at, closed_at, busy_after, events = self.run_engine(True)
        self.assertIsNotNone(cleared_at, [e.get("state") for e in events])
        self.assertLess(cleared_at - closed_at, 2.0)
        # an unconfirmed quiz candidate does not keep the room explorer paused
        self.assertTrue(busy_after)
        self.assertFalse(any(busy_after), busy_after)


if __name__ == "__main__":
    unittest.main()
