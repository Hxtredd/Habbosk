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

    def test_three_basura_even_half_hidden(self):
        """Owner's screenshot (2026-11-12, with the owner's red pen circles cutting
        two of the bubbles): 4 desks, the ammunition and 3 Basura."""
        ms = self.labels("room_three_basura")
        bubbles = [m for m in ms if m.kind == "bubble"]
        self.assertEqual(len(bubbles), 8, [(m.label, m.box) for m in bubbles])
        self.assertGreaterEqual([m.label for m in bubbles].count("Basura"), 2)
        self.assertEqual([m.label for m in bubbles].count("Munición"), 1)

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

    def test_night(self):
        """Owner's screenshot at night in the game (2026-11-12): darker room,
        dimmer markers and labels - SK had seen no marker at all."""
        ms = self.labels("night_room")
        self.assertEqual([m.kind for m in ms].count("bubble"), 5)       # 4 desks + the ammunition
        self.assertEqual([m.kind for m in ms].count("arrow"), 1)
        self.assertGreaterEqual([m.label for m in ms].count("Tarea"), 3)
        self.assertIn("Munición", [m.label for m in ms if m.kind == "bubble"])
        e = V.read_energy(pic("night_energy_109"), self.digits, top_part=1.0)
        self.assertEqual(e.value, 109)
        self.assertAlmostEqual(e.fraction * 150, 109, delta=8)            # the bar agrees
        p = self.panel("night_panel")
        self.assertEqual(p.active, "Colocar la munición")                # "En curso"
        self.assertTrue(p.going)

    def test_name_of_the_selected_object(self):
        words = lambda im, psm: self.ocr.words(im, psm)
        img = pic("popup_armero_colocar")
        btns = V.yellow_buttons(img)
        self.assertEqual(len(btns), 1)
        name = V.selected_name(img, btns[0], words)
        self.assertTrue(V.is_gun_rack(name), name)
        img = pic("popup_coger")                              # the ammunition boxes: not the rack
        name = V.selected_name(img, V.yellow_buttons(img)[0], words)
        self.assertFalse(V.is_gun_rack(name), name)
        self.assertFalse(V.is_gun_rack("Mostrador"))

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
        self.selected = ""               # name in the window a click opened
        self.rack_after = []             # what the next clicks on the counter select
        self.wrong_placed = False

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
        elif self.phase == "carrying" and self.task == "Colocar la munición" and not self.popup \
                and abs(x - 708) < 30:
            # the click selects something on the counter: the rack, or the desk
            self.selected = self.rack_after.pop(0) if self.rack_after else "Armero para Pistolas"
            self.popup = True
        elif self.phase == "carrying" and self.task == "Colocar la munición" and self.popup:
            if (x, y) == V.button_click_point(self.button()) and self.selected == "Armero para Pistolas":
                self.popup = False
                if self.money:
                    self.phase = "money"
                else:
                    self.phase, self.remaining = "offers", self.remaining - 1
            elif abs(x - 708) < 30:
                self.selected = self.rack_after.pop(0) if self.rack_after else "Armero para Pistolas"
            elif (x, y) == V.button_click_point(self.button()):
                self.wrong_placed = True                     # "Colocar" pressed on the desk
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
        self.esc = []                    # times the owner presses Esc
        self.esc_pending = False         # an Esc pressed before SK started
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

    def stop_key(self):
        if self.esc_pending:
            self.esc_pending = False
            return True
        hit = [t for t in self.esc if t <= self.clock()]
        self.esc = [t for t in self.esc if t > self.clock()]
        return bool(hit)

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
    with mock.patch.object(V, "yellow_buttons", side_effect=yellow_for(game)), \
            mock.patch.object(V, "selected_name", side_effect=lambda img, btn, w: game.selected):
        r.run()


class Helper(unittest.TestCase):
    def test_does_a_full_walking_task(self):
        r, g, d, c = make()
        run_for(r, g, 60)
        self.assertGreaterEqual(r.tasks_done, 2)
        self.assertEqual(g.clicks[0], (230, 300))           # "Ir" of the walking task, not Montar
        self.assertNotIn((230, 240), g.clicks)              # never the mini-game task
        self.assertEqual(r.stopped_reason, "test time over")

    def test_esc_stops_at_once(self):
        r, g, d, c = make()
        d.esc.append(c() + 5.0)                              # the owner presses Esc at +5 s
        run_for(r, g, 60)
        self.assertIn("pressed Esc", r.stopped_reason)
        self.assertLess(c() - 1005.0, 0.5)                    # stopped right away

    def test_other_input_no_longer_stops_it(self):
        """Owner, 2026-11-12: it stopped with "you used the mouse or keyboard"
        when they hadn't - Windows' last-input time is not used any more."""
        r, g, d, c = make()
        real = d.click

        def click(x, y):                                      # input registered right after SK's click
            real(x, y)
            d.inputs.append(c() + 0.1)
        d.click = click
        run_for(r, g, 60)
        self.assertEqual(r.stopped_reason, "test time over")
        self.assertGreaterEqual(r.tasks_done, 2)

    def test_an_esc_pressed_before_starting_is_ignored(self):
        r, g, d, c = make()
        d.esc_pending = True                                  # Windows still remembers an old Esc
        run_for(r, g, 30)
        self.assertEqual(r.stopped_reason, "test time over")

    def test_no_clicks_while_the_owner_moves_the_mouse(self):
        r, g, d, c = make()
        start = c()
        # the owner moves the pointer from +4 s to +10 s, then lets go
        d.moves += [(start + 4.0 + 0.5 * k, (900 + 10 * k, 400)) for k in range(13)]
        times = []
        real = d.click

        def click(x, y):
            times.append(c())
            real(x, y)
        d.click = click
        run_for(r, g, 40)
        self.assertEqual(r.stopped_reason, "test time over")  # moving the mouse doesn't stop it
        self.assertFalse([t for t in times if start + 4.1 <= t < start + 12.0], times)   # rested 2 s first
        self.assertTrue([t for t in times if t >= start + 12.0])                        # then carries on
        self.assertTrue(any("using the mouse" in s.text for s in r.statuses))

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


class SeveralBasura(unittest.TestCase):
    """Owner, 2026-11-12: Barrer casquillos can show several "Basura" - all
    of them are picked up ("Llevas 1/3 de basura" ...), then the load goes to
    the bin. What to pick up is always under a "Basura" bubble."""

    SPOTS = [(500, 400), (800, 600), (1100, 460)]

    def setup(self, say_deliver_early=False, hidden=()):
        g = Game(task="Barrer casquillos")
        left = list(self.SPOTS)
        st = {"load": 0, "pending": None}

        def bubble(x, y):
            return V.Marker("bubble", (x, y, 22, 20), (x - 5, y + 30, 33, 13), "Basura")

        def panel():
            if g.phase == "offers":
                return V.PanelState(True, offers=[V.Offer("Barrer casquillos", "Barrer casquillos", True, (230, 300))],
                                    remaining=g.remaining)
            if g.phase == "going":
                n = st["load"]
                return V.PanelState(True, active="Barrer casquillos", going=n == 0, carrying=n > 0,
                                    load=(n, 3) if n else None, load_kind="Basura" if n else None,
                                    deliver=say_deliver_early and n > 0, step=(1, 2), remaining=g.remaining)
            if g.phase == "carrying":
                return V.PanelState(True, active="Barrer casquillos", carrying=True, load=(3, 3), load_kind="Basura",
                                    deliver=True, step=(2, 2), remaining=g.remaining)
            return V.PanelState(False)

        def markers():
            ms = [V.Marker("bubble", (300, 300, 22, 20), (295, 330, 33, 13), "Tarea")]
            ms += [bubble(x, y) for x, y in left]
            if g.phase == "carrying" or (g.phase == "going" and st["load"]):
                ms.append(V.Marker("arrow", (700, 300, 16, 22), (690, 330, 44, 13), None))   # the bin
            return ms

        def click(x, y):
            g.clicks.append((x, y))
            if g.phase == "offers" and (x, y) == (230, 300):
                g.phase = "going"
                return
            if g.phase == "going":
                if g.popup and (x, y) == V.button_click_point(g.button()):
                    g.popup = False
                    left.remove(st["pending"])
                    st["load"] += 1
                    if st["load"] == 3:
                        g.phase = "carrying"
                    return
                for spot in left:
                    if (x, y) == bubble(*spot).click_point and spot not in hidden:
                        g.popup, st["pending"] = True, spot
                        return
            if g.phase == "carrying" and abs(x - 712) < 30:
                g.phase, g.remaining = "offers", g.remaining - 1
                st["load"] = 0
                left[:] = list(self.SPOTS)
        def button():                                         # opens next to the clicked object
            x, y = st["pending"] or self.SPOTS[0]
            return (x - 60, y - 40, 160, 36)
        g.panel, g.markers, g.click, g.button = panel, markers, click, button
        r, g, d, c = make(game=g)
        return r, g

    def test_collects_all_three_then_goes_to_the_bin(self):
        r, g = self.setup()
        run_for(r, g, 60)
        self.assertGreaterEqual(r.tasks_done, 1, r.stopped_reason)
        bin_clicks = [i for i, (x, y) in enumerate(g.clicks) if abs(x - 712) < 30 and abs(y - 343) < 30]
        buttons = [i for i, p in enumerate(g.clicks)
                   if any(p == V.button_click_point((x - 60, y - 40, 160, 36)) for x, y in self.SPOTS)]
        self.assertGreaterEqual(len(buttons), 3)
        self.assertLess(buttons[2], bin_clicks[0])           # all three picked up before the bin

    def test_keeps_collecting_even_if_the_panel_already_says_take_it(self):
        r, g = self.setup(say_deliver_early=True)
        run_for(r, g, 60)
        self.assertGreaterEqual(r.tasks_done, 1, r.stopped_reason)

    def test_a_basura_without_button_is_skipped(self):
        r, g = self.setup(hidden=[(500, 400)])                 # this one gives no button
        with mock.patch.object(V, "find_objetos_search", return_value=None):
            r.ocr = SimpleNamespace(words=lambda img, psm=6: [])
            run_for(r, g, 30)
        # the other two were collected; then nothing else to pick up -> stops, never at the bin
        buttons = [p for p in g.clicks
                   if any(p == V.button_click_point((x - 60, y - 40, 160, 36)) for x, y in self.SPOTS)]
        self.assertEqual(len(buttons), 2)
        self.assertFalse(any(abs(x - 712) < 30 and abs(y - 343) < 30 for x, y in g.clicks))
        self.assertIn("no yellow button", r.stopped_reason)


class WhereToDrop(unittest.TestCase):
    """Owner (2026-11-12): ammunition onto the three guns (where the arrow is),
    trash to the bin (its label, as in the recordings)."""

    ARROW = V.Marker("arrow", (1074, 586, 18, 22), (1050, 612, 60, 13), None)

    def test_ammunition_goes_on_the_guns_under_the_arrow(self):
        p = V.PanelState(True, active="Colocar la munición", carrying=True, load=(1, 1), load_kind="Munición")
        self.assertEqual(R.WorkRunner.deliver_point(self.ARROW, p), (1083, 597))
        # even when the task name wasn't read, the load line says it's ammunition
        p = V.PanelState(True, carrying=True, load=(1, 1), load_kind="Munición")
        self.assertEqual(R.WorkRunner.deliver_point(self.ARROW, p), (1083, 597))

    def test_trash_goes_to_the_bin_label(self):
        p = V.PanelState(True, active="Barrer casquillos", carrying=True, load=(3, 3), load_kind="Basura")
        self.assertEqual(R.WorkRunner.deliver_point(self.ARROW, p), self.ARROW.click_point)

    def test_ammunition_pickup_clicks_the_boxes_under_the_upper_label(self):
        """Step 1: the boxes under the "!" bubble's label (owner's circle), not
        the floor ring of the arrow below them."""
        r, g, d, c = make()
        g.phase = "going"
        bubble = V.Marker("bubble", (720, 574, 22, 20), (708, 603, 43, 13), "Munición")
        arrow = V.Marker("arrow", (724, 630, 16, 22), (708, 660, 44, 13), "Munición")
        g.markers = lambda: [bubble, arrow]
        r._pause = lambda reason: r.stop("test: paused")
        run_for(r, g, 3)
        self.assertEqual(g.clicks[0], bubble.click_point)
        self.assertLess(g.clicks[0][1], arrow.box[1])        # above the arrow and its ring


class AfterPickingUpTheBoxes(unittest.TestCase):
    """Owner (2026-11-12): after the boxes are picked up ("Llevas 1/1 cajas")
    SK must go on to the Armero para Pistolas - it clicked the boxes again."""

    def run_round(self, panel_after):
        r, g, d, c = make()
        real_panel = g.panel

        def panel():
            q = real_panel()
            return panel_after(q) if g.phase == "carrying" else q
        g.panel = panel
        run_for(r, g, 40)
        return r, g

    def box_clicks_after_pickup(self, g):
        """Clicks on the boxes' marker between picking them up and the rack."""
        first_button = g.clicks.index(V.button_click_point(g.button()))
        rest = g.clicks[first_button + 1:]
        rack = [i for i, (x, y) in enumerate(rest) if abs(x - 708) < 30]
        upto = rest[:rack[0]] if rack else rest
        return [(x, y) for x, y in upto if abs(x - 312) < 30 and y < 360]

    def test_panel_lines_unread_still_goes_to_the_rack(self):
        # neither "Llevas 1/1 cajas" nor "Lleva la carga" read: only the task name
        r, g = self.run_round(lambda q: V.PanelState(True, active=q.active, remaining=q.remaining))
        self.assertGreaterEqual(r.tasks_done, 1, r.stopped_reason)
        self.assertEqual(self.box_clicks_after_pickup(g), [])

    def test_step_two_means_take_it_there(self):
        r, g = self.run_round(lambda q: V.PanelState(True, active=q.active, step=(2, 3), remaining=q.remaining))
        self.assertGreaterEqual(r.tasks_done, 1, r.stopped_reason)
        self.assertEqual(self.box_clicks_after_pickup(g), [])

    def test_nothing_read_but_the_panel(self):
        r, g = self.run_round(lambda q: V.PanelState(True, remaining=q.remaining))
        self.assertEqual(self.box_clicks_after_pickup(g), [])


class GunRack(unittest.TestCase):
    """Owner (2026-11-12): for Colocar la munición the click must select the
    "Armero para Pistolas" (check the window's name) - SK had clicked the desk."""

    def test_wrong_object_is_not_used_and_the_rack_is_tried_again(self):
        r, g, d, c = make()
        g.rack_after = ["Mostrador de armas", "Armero para Pistolas"]
        run_for(r, g, 30)
        self.assertGreaterEqual(r.tasks_done, 1, r.stopped_reason)
        self.assertFalse(g.wrong_placed)

    def test_never_the_rack_stops_without_pressing_colocar(self):
        r, g, d, c = make()
        g.rack_after = ["Mostrador de armas"] * 10
        run_for(r, g, 60)
        self.assertIn("couldn't select the Armero para Pistolas", r.stopped_reason)
        self.assertIn("Mostrador de armas", r.stopped_reason)
        self.assertFalse(g.wrong_placed)
        self.assertEqual(r.tasks_done, 0)

    def test_trash_still_goes_to_the_bin_label(self):
        p = V.PanelState(True, active="Barrer casquillos", carrying=True, load=(1, 1), load_kind="Basura")
        a = V.Marker("arrow", (1012, 746, 16, 22), (992, 776, 60, 13), "Contenedor")
        self.assertFalse(R.WorkRunner._is_ammo(a, p))


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
