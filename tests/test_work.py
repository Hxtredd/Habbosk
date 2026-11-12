"""Work helper (owner-approved 2026-11-11): reading the job screen and the
helper's decisions and safeguards.

Part 1 reads real pictures from the owner's recordings (tests/fixtures/work,
crops without chat or names). Part 2 runs the helper against a simulated
game and PC: every safeguard must stop or pause it.
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.work import runner as R  # noqa: E402
from puzzle_assistant.work import vision as V  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures", "work")


def pic(name):
    img = cv2.imread(os.path.join(FX, name + ".webp"))
    assert img is not None, name
    return img


# ---------------------------------------------------------------------------
# Part 1 - pictures (no OCR needed)
# ---------------------------------------------------------------------------
class MarkersAndButtons(unittest.TestCase):
    def kinds(self, name):
        ms = V.find_markers(pic(name))
        return [m.kind for m in ms]

    def test_pickup_screen_has_bubbles_and_one_arrow(self):
        k = self.kinds("room_pickup_municion")
        self.assertEqual(k.count("arrow"), 1)
        self.assertEqual(k.count("bubble"), 5)          # 4 desks ("Tarea") + the ammunition

    def test_deliver_screens_have_one_arrow(self):
        self.assertEqual(self.kinds("room_deliver_armero").count("arrow"), 1)
        self.assertEqual(self.kinds("room_deliver_contenedor").count("arrow"), 1)

    def test_trash_has_a_bubble_and_no_arrow(self):
        k = self.kinds("room_pickup_basura")
        self.assertEqual(k.count("arrow"), 0)
        self.assertEqual(k.count("bubble"), 5)

    def test_yellow_button_appears_after_the_click(self):
        before, after = pic("popup_before"), pic("popup_coger")
        self.assertEqual(V.yellow_buttons(before), [])
        btn = V.new_button_near(V.yellow_buttons(before), V.yellow_buttons(after), (130, 160))
        self.assertIsNotNone(btn)
        x, y, w, h = btn
        cx, cy = V.button_click_point(btn)
        self.assertTrue(x < cx < x + w and y < cy < y + h)
        # a button far away from the click is not taken
        self.assertIsNone(V.new_button_near([], V.yellow_buttons(after), (2000, 2000)))
        self.assertEqual(len(V.yellow_buttons(pic("popup_recoger"))), 1)

    def test_chat_box_found_when_the_name_bar_is_covered(self):
        from puzzle_assistant.work.desk import find_chat_box
        found = find_chat_box(pic("chat_bar"))
        self.assertIsNotNone(found)
        (x, y), h = found
        self.assertLess(abs(x - 138), 6)
        self.assertLess(abs(y - 337), 6)

    def test_label_matching_is_strict(self):
        self.assertEqual(V.match_label("Barura"), "Basura")
        self.assertEqual(V.match_label("Tarma"), "Tarea")
        self.assertEqual(V.match_label("Munición"), "Munición")
        self.assertIsNone(V.match_label("Ear"))
        self.assertIsNone(V.match_label(""))

    def test_energy_judge_uses_the_bar_against_misread_numbers(self):
        j = V.EnergyJudge(40)
        bar = (0, 0, 87, 8)
        self.assertEqual(j.judge(V.Energy(119 / 150, 119, bar, bar)), (False, 119))
        # "19" for 119: the bar says ~119 -> not low
        low, v = j.judge(V.Energy(119 / 150, 19, bar, bar))
        self.assertFalse(low)
        self.assertAlmostEqual(v, 119, delta=3)
        self.assertEqual(j.judge(V.Energy(35 / 150, 35, bar, bar)), (True, 35))
        self.assertEqual(V.EnergyJudge(40).judge(None), (None, None))


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class ReadingWithOcr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from puzzle_assistant.ocr import Tesseract
        cls.ocr = Tesseract(Config())

    def line(self, img):
        return self.ocr.text(img, 7)

    def digits(self, img):
        return self.ocr._run(img, 7, ["-c", "tessedit_char_whitelist=0123456789"], "txt")

    def labels(self, name):
        img = pic(name)
        ms = V.find_markers(img)
        V.read_labels(img, ms, self.line)
        return ms

    def test_marker_labels(self):
        ms = self.labels("room_pickup_municion")
        self.assertEqual([m.label for m in ms if m.kind == "bubble"].count("Munición"), 1)
        self.assertGreaterEqual([m.label for m in ms].count("Tarea"), 3)
        ms = self.labels("room_pickup_basura")
        self.assertEqual([m.label for m in ms].count("Basura"), 1)
        self.assertNotIn("Munición", [m.label for m in ms])

    def panel(self, name):
        img = pic(name)
        up = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        box = V.find_panel(self.ocr.words(up, 11), (0, 0), 2.0)
        self.assertIsNotNone(box, name)
        x, y, w, h = box
        crop = img[y:y + h, x:x + w]
        up = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        words = self.ocr.words(up, 11)
        for wd in words:
            wd.x, wd.y, wd.w, wd.h = wd.x // 2, wd.y // 2, wd.w // 2, wd.h // 2
        return V.parse_panel(words, w, crop)

    def test_panel_offers(self):
        p = self.panel("panel_offer_municion")
        self.assertFalse(p.busy)
        self.assertEqual(p.remaining, 49)
        tasks = {o.task: o for o in p.offers}
        self.assertTrue(tasks["Colocar la munición"].recommended)
        self.assertIsNotNone(tasks["Colocar la munición"].ir)
        self.assertIsNotNone(tasks["Montar un arma"].ir)
        p = self.panel("panel_offer_barrer")
        self.assertIn("Barrer casquillos", [o.task for o in p.offers])

    def test_ir_found_on_highlighted_cards(self):
        p = self.panel("panel_highlighted_ir")
        self.assertEqual(len(p.offers), 2)
        self.assertTrue(all(o.ir is not None for o in p.offers))
        # the owner's live screen (2026-11-12): teal "Ir" with a whole "r"
        p = self.panel("panel_live_teal_ir")
        self.assertEqual(len(p.offers), 2)
        self.assertTrue(all(o.ir is not None for o in p.offers), p.offers)
        self.assertEqual(p.remaining, 55)

    def test_panel_steps(self):
        p = self.panel("panel_going")
        self.assertEqual(p.active, "Colocar la munición")
        self.assertTrue(p.going)
        self.assertFalse(p.carrying)
        p = self.panel("panel_carrying")
        self.assertTrue(p.carrying and p.deliver)
        self.assertEqual(p.step, (2, 3))
        p = self.panel("panel_working_banco")
        self.assertEqual(p.active, "Limpiar el banco")
        self.assertTrue(p.working)

    def test_objetos_list_first_bala_below_the_search_box(self):
        img = pic("objetos_bala")
        words = lambda im, psm: self.ocr.words(im, psm)
        search = V.find_objetos_search(img, self.ocr.words(img, 11), words)
        self.assertIsNotNone(search)
        self.assertLess(abs(search[0] - 125) + abs(search[1] - 109), 12)   # the typed "bala"
        entries = V.objetos_entries(img, search, words)
        self.assertEqual(len(entries), 2)
        (x, y), (x2, y2) = entries
        self.assertGreater(y, search[1] + 20)                                # below the box
        self.assertLess(abs(y - 142), 5)
        self.assertLess(abs(y2 - 164), 5)
        # a picture without the Objetos window: nothing
        room = pic("room_pickup_basura")
        self.assertIsNone(V.find_objetos_search(room, self.ocr.words(room, 11), words))

    def test_energy(self):
        for name, value in (("energy_119", 119), ("energy_83", 83), ("energy_74", 74)):
            e = V.read_energy(pic(name), self.digits, top_part=1.0)
            self.assertIsNotNone(e, name)
            self.assertAlmostEqual(e.fraction * 150, value, delta=8)
            low, v = V.EnergyJudge(80).judge(e)
            self.assertEqual(v, value, name)
            self.assertEqual(low, value < 80)


# ---------------------------------------------------------------------------
# Part 2 - the helper against a simulated game and PC
# ---------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)


class Game:
    """A tiny job: offers -> Ir -> going (arrow) -> yellow button -> carrying
    -> arrow -> (money) -> done, remaining - 1."""

    def __init__(self, task="Colocar la munición", money=False):
        self.task, self.money = task, money
        self.phase = "offers"
        self.remaining = 50
        self.popup = False
        self.clicks = []
        self.chat = []
        self.energy = 120
        self.dead_clicks = False         # clicks do nothing

    def panel(self):
        if self.phase == "offers":
            return V.PanelState(True, offers=[
                V.Offer("Montar un arma", "Montar un arma", False, (230, 240)),
                V.Offer(self.task, self.task, True, (230, 300))], remaining=self.remaining)
        if self.phase == "going":
            return V.PanelState(True, active=self.task, going=True, step=(1, 3), remaining=self.remaining)
        if self.phase == "carrying":
            return V.PanelState(True, active=self.task, carrying=True, deliver=True, step=(2, 3),
                                remaining=self.remaining)
        if self.phase == "money":
            return V.PanelState(True, active=self.task, working=True, step=(3, 3), remaining=self.remaining)
        return V.PanelState(False)

    def markers(self):
        if self.phase == "going":
            return [V.Marker("bubble", (500, 500, 22, 20), (490, 530, 40, 13), "Tarea"),
                    V.Marker("arrow", (300, 300, 16, 22), (290, 330, 44, 13), V.WALKING_TASKS[self.task])]
        if self.phase == "carrying":
            return [V.Marker("arrow", (700, 300, 16, 22), (690, 330, 44, 13), None)]
        return []

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.dead_clicks:
            return
        if self.phase == "offers" and (x, y) == (230, 300):
            self.phase = "going"
        elif self.phase == "going" and not self.popup and abs(x - 312) < 30:
            self.popup = True
        elif self.phase == "going" and self.popup and (x, y) == V.button_click_point(self.button()):
            self.popup, self.phase = False, "carrying"
        elif self.phase == "carrying" and abs(x - 712) < 30:
            if self.money:
                self.phase = "money"
            else:
                self.phase, self.remaining = "offers", self.remaining - 1

    def button(self):
        return (250, 380, 160, 36)


class FakeDesk(R.Desk):
    def __init__(self, game, clock):
        self.game, self.clock = game, clock
        self.inputs = []                 # times the owner touched the mouse / keyboard
        self.pos = (5, 5)                # mouse pointer
        self.moves = []                  # (time, position): the owner moves the pointer
        self.typed = []
        self.beeps = 0

    def grab(self):
        return SimpleNamespace(shape=(1080, 1920, 3)), {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def click(self, x, y):
        self.pos = (x, y)
        self.game.click(x, y)

    def cursor(self):
        for t, p in self.moves:
            if t <= self.clock():
                self.pos = p
        self.moves = [(t, p) for t, p in self.moves if t > self.clock()]
        return self.pos

    def type_in_chat(self, text):
        self.typed.append(text)
        if text == ":beber":
            self.game.energy += 50
        return True

    def last_input(self):
        past = [t for t in self.inputs if t <= self.clock()]
        return max(past) if past else -1e9

    def beep(self):
        self.beeps += 1


class Sim(R.WorkRunner):
    """The helper with the game's screen reading replaced by the simulation."""

    def read_panel(self, img):
        return self.desk.game.panel()

    def markers(self, img):
        return self.desk.game.markers()

    def check_chat(self, img):
        g = self.desk.game
        name = self.cfg.work_my_name.lower()
        for line in g.chat:
            g.chat = []
            if name in line.lower() and not line.lower().startswith(name + ":"):
                return line
        return None

    def drink_if_low(self, img):
        g = self.desk.game

        def fake_read(img, ocr=None):                     # the game's current energy
            return V.Energy(min(1.0, g.energy / 150), g.energy, (0, 0, 87, 8), (0, 0, 1, 1))
        with mock.patch.object(V, "read_energy", side_effect=fake_read):
            super().drink_if_low(img)


def make(game=None, **cfg_over):
    clock = Clock()
    game = game or Game()
    desk = FakeDesk(game, clock)
    cfg = Config()
    cfg.work_my_name = "Owner"
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    statuses, asks = [], []
    r = Sim(cfg, desk, ocr=None, status=statuses.append, ask_presence=lambda: asks.append(clock()),
            clock=clock, sleep=clock.sleep)
    r.statuses, r.asks = statuses, asks
    return r, game, desk, clock


def yellow_for(game):
    return lambda img: [game.button()] if game.popup else []


def run_for(r, game, seconds):
    """Run the helper's loop in this thread until it stops or time passes."""
    end = r.clock() + seconds
    real_check = r._check_hands_off

    def check():
        real_check()
        if r.clock() >= end:
            r.stop("test time over")
    r._check_hands_off = check
    with mock.patch.object(V, "yellow_buttons", side_effect=yellow_for(game)):
        r.run()


class Helper(unittest.TestCase):
    def test_does_a_full_walking_task(self):
        r, g, d, c = make()
        run_for(r, g, 60)
        self.assertGreaterEqual(r.tasks_done, 2)
        self.assertEqual(g.clicks[0], (230, 300))           # "Ir" of the walking task, not Montar
        self.assertNotIn((230, 240), g.clicks)              # never the mini-game task
        self.assertEqual(r.stopped_reason, "test time over")

    def test_any_mouse_or_key_input_stops_it(self):
        r, g, d, c = make()
        d.inputs.append(c() + 5.0)                           # the owner moves the mouse at +5 s
        run_for(r, g, 60)
        self.assertIn("mouse or keyboard", r.stopped_reason)
        n = len(g.clicks)
        self.assertLess(c() - 1005.0, 1.0)                    # stopped right away
        self.assertEqual(len(g.clicks), n)

    def test_input_right_after_an_own_click_is_still_seen(self):
        r, g, d, c = make()
        real = d.click

        def click(x, y):                                       # the owner presses a key
            real(x, y)                                         # 0.1 s after SK's click
            d.inputs.append(c() + 0.1)
        d.click = click
        run_for(r, g, 60)
        self.assertIn("mouse or keyboard", r.stopped_reason)
        self.assertEqual(len(g.clicks), 1)

    def test_moving_the_mouse_stops(self):
        r, g, d, c = make()
        d.moves.append((c() + 7.0, (900, 400)))
        run_for(r, g, 60)
        self.assertIn("moved the mouse", r.stopped_reason)
        self.assertLess(c() - 1007.0, 0.5)

    def test_moving_away_after_answering_is_allowed(self):
        r, g, d, c = make(work_presence_min=1, work_presence_max=1)
        orig = r._ask_presence

        def answer():
            orig()
            d.moves.append((c() + 0.5, (2000, 300)))           # back from SK's window
            r.presence_answered()
        r._ask_presence = answer
        run_for(r, g, 100)
        self.assertEqual(r.stopped_reason, "test time over")

    def test_never_clicks_under_sks_own_window(self):
        r, g, d, c = make()
        r.sk_windows = [(200, 280, 100, 60)]                  # SK's window over the "Ir" button
        run_for(r, g, 30)
        self.assertIn("SK's window covers", r.stopped_reason)
        self.assertEqual(g.clicks, [])

    def test_presence_check_without_answer_stops(self):
        r, g, d, c = make(work_presence_min=1, work_presence_max=1)
        run_for(r, g, 600)
        self.assertEqual(len(r.asks), 1)
        self.assertIn("Sigues ahí", r.stopped_reason)
        self.assertLess(r.asks[0] - 1000.0, 75)

    def test_presence_check_answered_keeps_working(self):
        r, g, d, c = make(work_presence_min=1, work_presence_max=1)
        orig = r._ask_presence
        r._ask_presence = lambda: (orig(), r.presence_answered())
        run_for(r, g, 150)
        self.assertGreaterEqual(len(r.asks), 2)
        self.assertEqual(r.stopped_reason, "test time over")

    def test_session_time_limit(self):
        r, g, d, c = make(work_session_minutes=5, work_presence_min=99, work_presence_max=99)
        run_for(r, g, 3600)
        self.assertIn("time limit", r.stopped_reason)
        self.assertLess(c() - 1000.0, 5 * 60 + 30)

    def test_mention_in_chat_pauses_until_continue(self):
        r, g, d, c = make()
        g.chat = ["someone: hola Owner, ¿estás?"]
        r._continue_after = None
        real_pause = r._pause

        def pause(reason):
            r.paused_with = reason
            r.stop("test: paused")
        r._pause = pause
        run_for(r, g, 60)
        self.assertIn("mentioned you", r.paused_with)
        self.assertEqual(g.clicks, [])                       # nothing done before pausing
        del real_pause

    def test_own_chat_line_is_not_a_mention(self):
        r, g, d, c = make()
        g.chat = ["Owner: :beber"]
        run_for(r, g, 20)
        self.assertFalse(hasattr(r, "paused_with"))
        self.assertTrue(g.clicks)

    def test_click_that_does_nothing_stops(self):
        r, g, d, c = make()
        g.dead_clicks = True
        run_for(r, g, 120)
        self.assertIn("nothing happened", r.stopped_reason)

    def test_unsure_target_stops(self):
        r, g, d, c = make()
        g.phase = "going"
        g.markers = lambda: [V.Marker("arrow", (300, 300, 16, 22)), V.Marker("arrow", (600, 300, 16, 22))]
        run_for(r, g, 30)
        self.assertIn("not sure", r.stopped_reason)
        self.assertEqual(g.clicks, [])

    def test_no_marker_stops(self):
        r, g, d, c = make()
        g.phase = "going"
        g.markers = lambda: [V.Marker("bubble", (500, 500, 22, 20), None, "Tarea")]
        run_for(r, g, 30)
        self.assertIn("can't find", r.stopped_reason)
        self.assertEqual(g.clicks, [])                       # never clicks a "Tarea" desk

    def test_only_minigames_offered_pauses(self):
        r, g, d, c = make()
        g.panel = lambda: V.PanelState(True, offers=[V.Offer("Montar un arma", "Montar un arma", True, (230, 240))])
        r._pause = lambda reason: (setattr(r, "paused_with", reason), r.stop("test: paused"))
        run_for(r, g, 30)
        self.assertIn("mini-game", r.paused_with)
        self.assertEqual(g.clicks, [])

    def test_no_job_panel_stops(self):
        r, g, d, c = make()
        g.panel = lambda: V.PanelState(False)
        run_for(r, g, 30)
        self.assertIn("job panel", r.stopped_reason)

    def test_low_energy_types_beber_and_checks_it_rose(self):
        r, g, d, c = make(work_energy_min=40)
        g.energy = 30
        run_for(r, g, 30)
        self.assertEqual(d.typed, [":beber"])
        self.assertEqual(r.stopped_reason, "test time over")

    def test_beber_without_effect_stops(self):
        r, g, d, c = make(work_energy_min=40)
        g.energy = 30
        d.type_in_chat = lambda text: (d.typed.append(text), True)[1]   # energy doesn't rise
        run_for(r, g, 30)
        self.assertIn("didn't go up", r.stopped_reason)

    def test_energy_fine_types_nothing(self):
        r, g, d, c = make(work_energy_min=40)
        run_for(r, g, 30)
        self.assertEqual(d.typed, [])


class InvisibleBullets(unittest.TestCase):
    """Owner, 2026-11-12: some bullets can't be seen (game glitch); the Objetos
    window (with "bala" typed in) lists them: SK clicks the first "Bala" of
    the list, then the yellow button."""

    ENTRY, BUTTON = (1650, 142), (1600, 900, 180, 36)

    def setup(self, entries=True, button=True):
        g = Game(task="Barrer casquillos")
        g.markers = lambda: ([V.Marker("bubble", (500, 500, 22, 20), (490, 530, 40, 13), "Tarea")]
                             if g.phase == "going" else
                             [V.Marker("arrow", (700, 300, 16, 22), (690, 330, 44, 13), None)]
                             if g.phase == "carrying" else [])
        real_click = g.click

        def click(x, y):
            if g.phase == "going" and (x, y) == self.ENTRY and button:
                g.clicks.append((x, y))
                g.popup = True
                return
            real_click(x, y)
        g.click = click
        g.button = lambda: self.BUTTON
        r, g, d, c = make(game=g)
        search = mock.patch.object(V, "find_objetos_search", return_value=(1660, 109))
        listed = mock.patch.object(V, "objetos_entries",
                                   side_effect=lambda img, s, w: ([self.ENTRY, (1650, 164)]
                                                                  if entries and g.phase == "going" else []))
        for p in (search, listed):
            p.start()
            self.addCleanup(p.stop)
        r.ocr = SimpleNamespace(words=lambda img, psm=6: [])
        return r, g

    def test_picks_the_first_listed_bala_then_the_yellow_button(self):
        r, g = self.setup()
        run_for(r, g, 40)
        self.assertGreaterEqual(r.tasks_done, 1, r.stopped_reason)
        i = g.clicks.index(self.ENTRY)
        self.assertEqual(g.clicks[i + 1], V.button_click_point(self.BUTTON))
        self.assertNotIn((1660, 109), g.clicks)                 # never the typed "bala"

    def test_no_bullet_listed_stops_as_before(self):
        r, g = self.setup(entries=False)
        run_for(r, g, 40)
        self.assertIn("can't find", r.stopped_reason)

    def test_no_button_after_clicking_bala_stops(self):
        r, g = self.setup(button=False)
        run_for(r, g, 40)
        self.assertIn("Objetos list but no yellow button", r.stopped_reason)


class Money(unittest.TestCase):
    def state(self, total=49, verified=True, items=None, problems=()):
        items = items or [(1, True)] * 8
        return SimpleNamespace(total=total, done=True, verified=verified, items=items,
                               problems=list(problems))

    def place(self):
        return {"keypad": (800, 700, 360, 160), "input": (800, 650, 360, 36)}

    def test_keypad_points(self):
        k = R.keypad_points((800, 700, 360, 160))
        self.assertEqual(k["1"], (860.0, 720.0))
        self.assertEqual(k["0"], (980.0, 840.0))
        self.assertEqual(k["ok"], (1100.0, 840.0))
        self.assertIsNone(R.keypad_points((800, 700, 360, 110)))     # only 3 rows seen: not sure

    def run_money(self, st, shown="49"):
        r, g, d, c = make()
        r.on_money(st, self.place())
        r._pause = lambda reason: (setattr(r, "paused_with", reason), r.stop("test: paused"))
        with mock.patch.object(R, "read_input_digits", return_value=shown):
            r.step(None, {"left": 0, "top": 0})
        return r, g

    def test_types_a_sure_total_and_presses_ok(self):
        r, g = self.run_money(self.state())
        k = R.keypad_points(self.place()["keypad"])
        self.assertEqual(g.clicks, [k["4"], k["9"], k["ok"]])
        self.assertFalse(hasattr(r, "paused_with"))

    def test_unsure_count_is_left_to_the_owner(self):
        r, g = self.run_money(self.state(verified=False, problems=["item 3 unclear (read 5)"]))
        self.assertEqual(g.clicks, [])
        self.assertIn("not sure", r.paused_with)

    def test_wrong_digits_in_the_box_are_cleared_not_sent(self):
        r, g = self.run_money(self.state(), shown="4")
        k = R.keypad_points(self.place()["keypad"])
        self.assertNotIn(k["ok"], g.clicks)
        self.assertEqual(g.clicks[-2:], [k["del"], k["del"]])
        self.assertIn("box shows", r.paused_with)

    def test_unfinished_count_is_ignored(self):
        r, g, d, c = make()
        r.on_money(SimpleNamespace(done=False), self.place())
        self.assertIsNone(r.money)


if __name__ == "__main__":
    unittest.main()
