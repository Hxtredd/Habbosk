"""'Cuenta el dinero': replays of two real rounds (tests/fixtures/money_*.npz,
the puzzle area only, 10 frames per second)."""
import os
import sys
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.locate import search  # noqa: E402
from puzzle_assistant.money import (MoneyTracker, locate_money, parse_vistos,  # noqa: E402
                                    read_legend)
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.vision import prep_for_ocr, to_gray  # noqa: E402

ROUNDS = {
    "money_coins_125": dict(items=[25, 5, 10, 25, 5, 10, 25, 10, 10], total=125,
                            legend=[5, 10, 25], expected=9),
    "money_bills_390": dict(items=[100, 20, 50, 100, 20, 50, 50], total=390,
                            legend=[20, 50, 100], expected=7),   # game accepted 390
    # the owner's round of 2026-11-11 (15 frames a second): SK showed 129 on the
    # owner's PC and the game said "129 no es correcto" - a coin counted twice
    # after SK briefly did not look at the screen
    "money_coins_128": dict(items=[5, 5, 5, 5, 5, 50, 1, 1, 1, 50], total=128,
                            legend=[1, 5, 50], expected=10),
}


def load(name):
    d = np.load(os.path.join(HERE, "fixtures", name + ".npz"))
    return d["frames"], d["t"], tuple(d["strip"]), tuple(d["band"])


def replay(name, ocr=None):
    frames, times, (sx, sy, sw, sh), (bx, by, bw, bh) = load(name)
    tr = MoneyTracker()
    for k, (f, t) in enumerate(zip(frames, times)):
        tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        if k % 4 == 0:
            band = f[by:by + bh, bx:bx + bw]
            tr.set_legend(read_legend(band))
            if ocr is not None:
                text = ocr.text(prep_for_ocr(band[:, :int(bw * 0.45)], 3.0), psm=7)
                tr.set_vistos(parse_vistos(text))
    return tr.state(float(times[-1]) + 1.0)


class MoneyTests(unittest.TestCase):
    def test_items_and_total(self):
        for name, want in ROUNDS.items():
            st = replay(name)
            self.assertEqual([v for v, _ in st.items], want["items"], name)
            self.assertTrue(all(s for _, s in st.items), name)
            self.assertEqual(st.total, want["total"], name)
            self.assertEqual(st.legend, want["legend"], name)
            self.assertFalse(st.done, "without the Vistos counter it must not claim done")

    def test_locate(self):
        for name in ROUNDS:
            img = cv2.imread(os.path.join(HERE, "fixtures", name + "_panel.png"))
            p = locate_money(to_gray(img))
            self.assertIsNotNone(p, name)
            self.assertIsNone(search(img, to_gray(img), Config()), "not a weapon puzzle")
        for fx in ("puzzle_8_pieces.png", "puzzle_7_pieces.png"):
            img = cv2.imread(os.path.join(HERE, "fixtures", fx))
            self.assertIsNone(locate_money(to_gray(img)), fx)

    def test_parse_vistos(self):
        self.assertEqual(parse_vistos("Vistos 3/9"), (3, 9))
        self.assertEqual(parse_vistos("vistos 7 / 7"), (7, 7))
        self.assertIsNone(parse_vistos("Vistos"))

    def test_missed_item_is_not_verified(self):
        frames, times, (sx, sy, sw, sh), _ = load("money_bills_390")
        tr = MoneyTracker()
        for k, (f, t) in enumerate(zip(frames, times)):
            if 30 <= k < 52:            # app "blind" for 2.2 s: one bill is missed
                continue
            tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        tr.set_vistos((7, 7))
        st = tr.state(float(times[-1]) + 1.0)
        self.assertFalse(st.verified)


    def replay_with_pause(self, name, pause_from, pause_len):
        frames, times, (sx, sy, sw, sh), _ = load(name)
        tr = MoneyTracker()
        for f, t in zip(frames, times):
            if pause_from <= t < pause_from + pause_len:   # SK busy: not looking
                continue
            tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        want = ROUNDS[name]
        tr.set_legend(want["legend"])
        tr.set_vistos((want["expected"], want["expected"]))
        return tr.state(float(times[-1]) + 1.0)

    def test_a_short_pause_never_counts_an_item_twice(self):
        """v3.5: items only enter at the left; an item SK lost sight of for a moment
        is picked up again instead of being counted again (129 instead of 128)."""
        for name, want in ROUNDS.items():
            _, times, _, _ = load(name)
            start, end = float(times[0]), float(times[-1])
            for pause, step in ((0.4, 0.5), (1.0, 0.7)):
                at = start + 0.5
                while at < end - pause:
                    st = self.replay_with_pause(name, at, pause)
                    self.assertEqual((st.total, st.counted), (want["total"], want["expected"]),
                                     f"{name}: pause {pause} s at {at:.1f} s -> {st.total} ({st.problems})")
                    self.assertTrue(st.verified, (name, pause, at, st.problems))
                    at += step

    def test_a_wrong_total_is_never_verified(self):
        """Whatever goes wrong, a total that isn't right must not be shown as verified."""
        for name, want in ROUNDS.items():
            _, times, _, _ = load(name)
            for pause in (1.6, 2.2):                       # long enough to miss items
                at = float(times[0]) + 0.5
                while at < float(times[-1]) - pause:
                    st = self.replay_with_pause(name, at, pause)
                    if st.total != want["total"]:
                        self.assertFalse(st.verified, (name, pause, at, st.total))
                    at += 0.9

    def test_starting_late_still_counts_correctly(self):
        """SK starts watching when the first coin is already half-way: it entered
        before SK looked, so it is a real coin - counted, and the game's counter
        confirms the count."""
        frames, times, (sx, sy, sw, sh), _ = load("money_coins_128")
        tr = MoneyTracker()
        for f, t in zip(frames, times):
            if t < 4.3:                                    # first coin entered at ~3.3 s
                continue
            tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        tr.set_legend([1, 5, 50])
        tr.set_vistos((10, 10))
        st = tr.state(float(times[-1]) + 1.0)
        self.assertEqual((st.total, st.counted), (128, 10))
        self.assertTrue(st.verified, st.problems)

    def test_item_appearing_mid_strip_while_watching_is_flagged(self):
        """An item that pops up in the middle while SK WAS watching all along can't
        be explained: the result is not verified."""
        frames, times, (sx, sy, sw, sh), _ = load("money_coins_128")
        tr = MoneyTracker()
        for f, t in zip(frames, times):
            strip = f[sy:sy + sh, sx:sx + sw].copy()
            if t < 3.9:                                    # hide the first coin's entry
                strip[:] = np.median(strip.reshape(-1, 3), axis=0).astype(strip.dtype)
            tr.update(strip, float(t))
        tr.set_vistos((10, 10))
        st = tr.state(float(times[-1]) + 1.0)
        self.assertFalse(st.verified)
        self.assertTrue(any("middle of the strip" in p for p in st.problems), st.problems)

    def test_frozen_after_done(self):
        frames, times, (sx, sy, sw, sh), _ = load("money_bills_390")
        tr = MoneyTracker()
        for f, t in zip(frames, times):
            tr.update(f[sy:sy + sh, sx:sx + sw], float(t))
        tr.set_vistos((7, 7))
        end = float(times[-1]) + 1.0
        st = tr.state(end)
        self.assertTrue(st.done and st.verified)
        # the puzzle closes: whatever is behind the strip must not be counted
        for k in range(20):
            tr.update(frames[k][sy:sy + sh, sx:sx + sw], end + 0.1 * k)
        tr.set_vistos((1, 9))
        self.assertEqual(tr.state(end + 3).total, 390)


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class MoneyEngineTests(unittest.TestCase):
    """v3.5: SK's real engine on the owner's round, played in real time; the
    screen capture freezes once for 0.6 s while coins cross (a busy PC). The
    engine must time each picture by when it was taken, so the coins are not
    counted twice (the owner saw 129 instead of 128)."""

    def test_slow_screen_capture_does_not_count_coins_twice(self):
        import queue
        import threading
        import time
        from unittest import mock
        from puzzle_assistant import engine as E
        frames, times, _, _ = load("money_coins_128")
        panel = cv2.imread(os.path.join(HERE, "fixtures", "money_coins_128_panel.png"))
        ox, oy = 15, 67                                    # the recorded area inside the panel
        bounds = {"left": 0, "top": 0, "width": 640, "height": 480}
        start = [None]
        stalled = []

        def screen():
            img = np.full((480, 640, 3), 30, np.uint8)
            t = 0.0 if start[0] is None else time.monotonic() - start[0]
            k = min(int(np.searchsorted(times - times[0], t)), len(frames) - 1)
            p = panel.copy()
            fh, fw = frames[k].shape[:2]
            p[oy:oy + fh, ox:ox + fw] = frames[k]
            img[40:40 + p.shape[0], 100:100 + p.shape[1]] = p
            return img, t

        class Grabber:
            def __init__(self, monitor=0):
                self.screens, self.monitor = [bounds], bounds

            def targets(self, region):
                return [bounds]

            def grab_bounds(self, b):
                if start[0] is None:
                    start[0] = time.monotonic()
                img, t = screen()
                if 6.4 <= t and not stalled:               # fixture time ~8.6 s: three coins on the strip
                    stalled.append(t)
                    time.sleep(0.6)
                    img, t = screen()                      # the picture arrives late
                x, y, w, h = b["left"], b["top"], b["width"], b["height"]
                return np.ascontiguousarray(img[y:y + h, x:x + w]), b

            def close(self):
                pass

        cfg = Config()
        cfg.region, cfg.questions, cfg.weapon_puzzle = None, False, False
        out = queue.Queue()
        with mock.patch.object(E, "ScreenGrabber", Grabber):
            eng = E.Engine(cfg, out)
            eng.start()
            last = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    m = out.get(timeout=0.5)
                except queue.Empty:
                    if start[0] is not None and time.monotonic() - start[0] > float(times[-1] - times[0]) + 2:
                        break
                    continue
                if m.get("state") == "money":
                    last = m["money"]
            eng.stop()
        self.assertTrue(stalled, "the capture never stalled")
        self.assertIsNotNone(last)
        self.assertEqual((last.total, last.counted), (128, 10), last.problems)
        self.assertTrue(last.done and last.verified, last.problems)


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class MoneyVerifiedTests(unittest.TestCase):
    def test_verified_with_vistos(self):
        from puzzle_assistant.ocr import Tesseract
        ocr = Tesseract(Config())
        for name, want in ROUNDS.items():
            st = replay(name, ocr)
            self.assertEqual((st.seen, st.expected), (want["expected"], want["expected"]), name)
            self.assertTrue(st.done and st.verified, (name, st.problems))
            self.assertEqual(st.total, want["total"])


if __name__ == "__main__":
    unittest.main()
