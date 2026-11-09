"""World Atlas stage 2 - Go to Room: finding the chat box, checking it has
the keyboard (gold border), and one trip per click with arrival check.
Real mouse / keyboard input is replaced by a recorder here."""
import os
import sys
import tempfile
import threading
import time
import types
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.world_atlas import navigate as N  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402
from puzzle_assistant.world_atlas.explorer import Tracker  # noqa: E402

FX = os.path.join(HERE, "fixtures", "atlas")
IDLE = cv2.imread(os.path.join(FX, "chat_idle.png"))
TYPING = cv2.imread(os.path.join(FX, "chat_typing.png"))


class ChatBoxTests(unittest.TestCase):
    def test_finds_chat_box_and_focus(self):
        icon = N.find_chat_input(IDLE)
        self.assertIsNotNone(icon)
        self.assertTrue(680 <= icon[0] <= 710, icon)         # the pink chat icon
        self.assertFalse(N.chat_focused(IDLE, icon))         # idle: grey border
        self.assertTrue(N.chat_focused(TYPING, N.find_chat_input(TYPING)))   # gold border


class FakeWin:
    def __init__(self):
        self.log = []

    def cursor(self):
        return (5, 5)

    def click(self, x, y):
        self.log.append(("click", x, y))

    def type_text(self, t):
        self.log.append(("type", t))

    def enter(self):
        self.log.append(("enter",))

    def move(self, x, y):
        self.log.append(("move", x, y))


class TripTests(unittest.TestCase):
    def setUp(self):
        self.atlas = Atlas(os.path.join(tempfile.mkdtemp(), "a.db"))
        self.atlas.import_input()
        self.tr = Tracker(self.atlas)
        self.tr.current = {"room_id": 13, "name": "Ayuntamiento de Ensenada"}
        view = types.SimpleNamespace(in_room=True)
        self.explorer = types.SimpleNamespace(tracker=self.tr, view=view,
                                              screen={"left": 100, "top": 0})
        self.win = FakeWin()
        self.saved = (N._Win, N.sys)
        N._Win = lambda: self.win
        N.sys = types.SimpleNamespace(platform="win32")

    def tearDown(self):
        N._Win, N.sys = self.saved
        self.atlas.close()

    def run_trip(self, frames, room_id, arrive=None):
        nav = N.Navigator(self.explorer, self.atlas)
        it = iter(frames)
        nav._grab = lambda: (next(it), self.explorer.screen)
        msgs = []
        done = threading.Event()

        def status(text, kind):
            msgs.append((text, kind))
            if kind in ("ok", "fail"):
                done.set()
        if arrive:
            def later():
                time.sleep(0.6)
                self.tr.current = arrive
            threading.Thread(target=later, daemon=True).start()
        nav.go(room_id, status)
        done.wait(12)
        return nav, msgs

    def test_one_click_one_command_and_arrival(self):
        nav, msgs = self.run_trip([IDLE, TYPING], 4, {"room_id": 4, "name": "Supermercado Eleven"})
        self.assertEqual(msgs[-1][1], "ok")
        self.assertIn("Supermercado Eleven", msgs[-1][0])
        typed = [e for e in self.win.log if e[0] in ("type", "enter")]
        self.assertEqual(typed, [("type", ":taxi 4"), ("enter",)])
        click = [e for e in self.win.log if e[0] == "click"][0]
        self.assertGreater(click[1], 100 + 690)                 # inside the chat box, right of the icon
        self.assertEqual(self.win.log[-1], ("move", 5, 5))      # mouse put back
        ok, why = nav.available()                               # taxi cooldown
        self.assertFalse(ok)
        self.assertIn("Wait", why)

    def test_nothing_typed_when_chat_box_does_not_take_the_keyboard(self):
        _, msgs = self.run_trip([IDLE, IDLE], 4)
        self.assertEqual(msgs[-1][1], "fail")
        self.assertFalse([e for e in self.win.log if e[0] in ("type", "enter")])

    def test_a_click_right_before_typing(self):
        # v3.3 (owner's request): the chat box is clicked again just before typing
        nav, msgs = self.run_trip([IDLE, TYPING], 4, {"room_id": 4, "name": "Supermercado Eleven"})
        self.assertEqual(msgs[-1][1], "ok")
        kinds = [e[0] for e in self.win.log]
        self.assertEqual(kinds[kinds.index("type") - 1], "click")
        self.assertEqual(kinds.count("click"), 2)
        self.assertEqual(kinds[-3:], ["type", "enter", "move"])

    def test_a_second_try_when_the_first_click_does_not_take(self):
        # the first click only brought the browser forward: SK clicks again
        nav, msgs = self.run_trip([IDLE, IDLE, TYPING], 4,
                                  {"room_id": 4, "name": "Supermercado Eleven"})
        self.assertEqual(msgs[-1][1], "ok")
        typed = [e for e in self.win.log if e[0] in ("type", "enter")]
        self.assertEqual(typed, [("type", ":taxi 4"), ("enter",)])
        self.assertEqual([e[0] for e in self.win.log].count("click"), 3)

    def test_nothing_typed_after_two_failed_tries(self):
        _, msgs = self.run_trip([IDLE, IDLE, IDLE], 4)
        self.assertEqual(msgs[-1][1], "fail")
        self.assertIn("didn't take the keyboard", msgs[-1][0])
        self.assertFalse([e for e in self.win.log if e[0] in ("type", "enter")])

    def test_already_there(self):
        _, msgs = self.run_trip([IDLE], 13)
        self.assertEqual(msgs[-1][1], "ok")
        self.assertEqual(self.win.log, [])

    def test_not_available_without_the_game(self):
        self.explorer.view = types.SimpleNamespace(in_room=False)
        nav = N.Navigator(self.explorer, self.atlas)
        self.assertFalse(nav.available()[0])
        N.sys = types.SimpleNamespace(platform="linux")
        self.assertIn("Windows", nav.available()[1])


if __name__ == "__main__":
    unittest.main()
