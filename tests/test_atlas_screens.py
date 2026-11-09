"""v3.1: the room-name bar is the hotel's only when the chat input (pink chat
icon) is right below it - a yellow emoji next to a dark area in Discord on the
other monitor made SK read the wrong screen (no room recognised, "Chat box not
found"). Go to Room now looks on every monitor. Synthetic decoys only (no real
screenshots of other programs are kept)."""
import os
import sys
import unittest
from unittest import mock

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.world_atlas import navigate as N  # noqa: E402
from puzzle_assistant.world_atlas import recognize as R  # noqa: E402

FX = os.path.join(HERE, "fixtures", "atlas")
ROOM = cv2.imread(os.path.join(FX, "cf63b70d.png"))          # Gran Casino, a real room view
YELLOW = (40, 200, 250)                                     # BGR: the pin's yellow


def discord_like(h=1080, w=1920):
    """A dark chat app with a yellow emoji (crown-like) next to a dark area
    in the lower half - the shape that used to pass as the hotel's pin."""
    img = np.full((h, w, 3), 35, np.uint8)
    cv2.rectangle(img, (618, 944), (633, 961), YELLOW, -1)      # 16 x 18, pin-sized
    cv2.putText(img, "staroth  6:23 PM", (660, 958), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (200, 200, 200), 1)
    return img


class NameBarNeedsTheChat(unittest.TestCase):
    def test_a_yellow_emoji_in_another_program_is_not_a_room(self):
        img = discord_like()
        self.assertIsNone(R.find_name_bar(img))
        self.assertIsNone(N.find_chat_input(img))

    def test_the_real_bar_is_still_found(self):
        nb = R.find_name_bar(ROOM)
        self.assertIsNotNone(nb)
        self.assertIsNotNone(R.chat_icon_below(ROOM, nb))

    def test_a_decoy_on_the_game_screen_does_not_win(self):
        img = ROOM.copy()
        H, W = img.shape[:2]
        cv2.rectangle(img, (40, H - 60), (55, H - 42), YELLOW, -1)   # lower, but no chat under it
        self.assertEqual(R.find_name_bar(img), R.find_name_bar(ROOM))

    def test_without_the_chat_input_there_is_no_name_bar(self):
        img = ROOM.copy()
        nb = R.find_name_bar(ROOM)
        ix, iy = R.chat_icon_below(ROOM, nb)
        img[iy - 15:iy + 15, ix - 15:ix + 15] = 35                    # chat icon covered
        self.assertIsNone(R.find_name_bar(img))


class GoToRoomLooksOnEveryMonitor(unittest.TestCase):
    def nav(self, explorer_screen):
        ex = mock.Mock()
        ex.screen = explorer_screen
        return N.Navigator(ex, mock.Mock())

    def test_the_chat_is_found_on_the_other_monitor(self):
        left = {"left": -1920, "top": -143, "width": 1920, "height": 1080}
        right = {"left": 0, "top": 0, "width": ROOM.shape[1], "height": ROOM.shape[0]}
        shots = {(-1920, -143): discord_like(), (0, 0): ROOM}

        class FakeMss:
            monitors = [{"left": -1920, "top": -143, "width": 3840, "height": 1223}, left, right]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        nav = self.nav(left)                                      # SK was looking at the wrong one
        grab_at = lambda self, b: (shots[(b["left"], b["top"])], b)  # noqa: E731
        with mock.patch("puzzle_assistant.capture.new_mss", return_value=FakeMss()), \
                mock.patch.object(N.Navigator, "_grab_at", grab_at):
            b, nb, icon, same = nav._find_chat()
        self.assertEqual((b, same), (right, False))
        self.assertEqual(nb, R.find_name_bar(ROOM))
        self.assertEqual(icon, R.chat_icon_below(ROOM, nb))

    def test_nothing_anywhere_means_nothing_is_typed(self):
        only = {"left": 0, "top": 0, "width": 1920, "height": 1080}

        class FakeMss:
            monitors = [only, only]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        nav = self.nav(only)
        with mock.patch("puzzle_assistant.capture.new_mss", return_value=FakeMss()), \
                mock.patch.object(N.Navigator, "_grab_at", lambda self, b: (discord_like(), b)):
            self.assertEqual(nav._find_chat(), (None, None, None, False))


if __name__ == "__main__":
    unittest.main()
