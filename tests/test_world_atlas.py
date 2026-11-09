"""World Atlas stage 1: reading room name / ID (read-only), the room-change
tracker (walk vs taxi), the database (approval, corrections, one-way
connections) and the first-start import of the calibration data."""
import json
import os
import re
import sys
import tempfile
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas, guess_kind  # noqa: E402
from puzzle_assistant.world_atlas.explorer import Tracker  # noqa: E402
from puzzle_assistant.world_atlas.recognize import RoomView, flatness  # noqa: E402

FX = os.path.join(HERE, "fixtures", "atlas")
N = lambda s: re.sub(r"[\s·\-]", "", (s or "").lower())  # noqa: E731


def new_atlas():
    a = Atlas(os.path.join(tempfile.mkdtemp(), "atlas.db"))
    a.import_input()
    return a


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class ReadRoomTests(unittest.TestCase):
    def test_name_and_id_from_real_screens(self):
        from puzzle_assistant.ocr import Tesseract
        from puzzle_assistant.world_atlas.recognize import read_view
        ocr = Tesseract(Config())
        for k, want in json.load(open(os.path.join(FX, "expected.json"), encoding="utf-8")).items():
            v = read_view(cv2.imread(os.path.join(FX, k + ".png")), ocr)
            self.assertEqual(v.room_id, want["room_id"], k)
            self.assertEqual(N(v.name), N(want["name"]), k)

    def test_nothing_on_an_empty_screen(self):
        from puzzle_assistant.ocr import Tesseract
        from puzzle_assistant.world_atlas.recognize import read_view
        v = read_view(np.full((1080, 1920, 3), 30, np.uint8), Tesseract(Config()))
        self.assertFalse(v.in_room)
        self.assertIsNone(v.room_id)


class TrackerTests(unittest.TestCase):
    def replay(self, name):
        d = np.load(os.path.join(FX, name + ".npz"))
        a = new_atlas()
        tr = Tracker(a)
        events = []
        for i, (t, (in_room, rid, nm)) in enumerate(zip(d["thumbs"], json.loads(str(d["readings"])))):
            tr.motion(t, i * 0.25)
            ev = tr.feed(RoomView(name=nm or None, room_id=None if rid < 0 else rid,
                                  in_room=in_room))
            if ev:
                events.append(ev)
        return a, tr, events

    def test_walk_records_a_one_way_connection(self):
        a, tr, ev = self.replay("walk_13_to_41")
        self.assertEqual([(e["room_id"], e["method"]) for e in ev], [(13, "start"), (41, "walk")])
        # the same walk was already known from the recording: seen twice now
        [c] = [c for c in a.connections(41) if c["method"] == "walk"]
        self.assertEqual((c["from_room"], c["to_room"], c["status"], c["times"]),
                         (13, 41, "observed", 2))
        self.assertIn("walked", tr.last_change)

    def test_taxi_is_a_visit_not_a_route(self):
        a, tr, ev = self.replay("taxi_13_to_4")
        self.assertEqual([(e["room_id"], e["method"]) for e in ev], [(13, "start"), (4, "taxi")])
        self.assertEqual([c for c in a.connections() if c["evidence"] == "explorer"], [])
        self.assertEqual(a.room(4)["visits"], 1)

    def test_flat_room_after_taxi_is_not_a_walk(self):
        # a taxi into a room with a plain background looks like a loading
        # screen, but nothing changes afterwards: still a taxi trip
        a = new_atlas()
        tr = Tracker(a)
        room = np.random.RandomState(1).randint(0, 255, (54, 96)).astype(np.uint8)
        flat = np.full((54, 96), 150, np.uint8)
        t = 0.0
        # (v3.3: the rooms' real names - an ID whose known room has another name is
        # no longer trusted, see test_atlas_stale_id.py)
        for img, view in [(room, RoomView(name="Ayuntamiento de Ensenada", room_id=13,
                                          in_room=True))] * 3 + \
                         [(flat, RoomView(name="El Bingo", room_id=65, in_room=True))] * 16:
            tr.motion(img, t)
            tr.feed(view)
            t += 0.25
        self.assertEqual(tr.current["room_id"], 65)
        self.assertEqual([c for c in a.connections() if c["evidence"] == "explorer"], [])
        self.assertGreater(flatness(flat), 0.9)

    def feed(self, tr, name, rid=None, n=3):
        ev = None
        for _ in range(n):
            ev = tr.feed(RoomView(name=name, room_id=rid, in_room=True)) or ev
        return ev

    def test_known_name_identifies_the_room_without_the_share_panel(self):
        a = new_atlas()
        tr = Tracker(a)
        ev = self.feed(tr, "Gran Casino")
        self.assertEqual((ev["room_id"], ev["by"]), (12411, "name"))
        ev = self.feed(tr, "Gran Casin0")                 # a small reading slip: same room
        self.assertIsNone(ev)
        self.assertEqual(a.room(12411)["visits"], 1)

    def test_unknown_name_asks_for_the_id_once(self):
        a = new_atlas()
        tr = Tracker(a)
        self.feed(tr, "Gran Casino")
        tr._loading_since_room = True                      # walked through an arrow
        ev = self.feed(tr, "Sala VIP del Casino")
        self.assertEqual(ev["event"], "need_id")
        self.assertIn("add its ID", tr.state)
        [p] = a.pending()
        self.assertIsNone(p["room_id"])
        self.assertEqual(a.accept(p["id"]), "need_id")     # an ID must be typed
        self.assertEqual(a.accept(p["id"], 13001, "Sala VIP del Casino"), "ok")
        self.assertEqual(a.room(13001)["name"], "Sala VIP del Casino")
        [c] = [c for c in a.connections(13001) if c["method"] == "walk"]
        self.assertEqual((c["from_room"], c["to_room"]), (12411, 13001))
        # next time the name alone is enough
        tr2 = Tracker(a)
        self.assertEqual(self.feed(tr2, "Sala VIP del Casino")["room_id"], 13001)

    def test_name_shared_by_two_rooms_needs_the_id(self):
        a = new_atlas()
        a.add_room(9001, "Avenida Alameda")                 # same name as room 41
        tr = Tracker(a)
        self.assertEqual(self.feed(tr, "Avenida Alameda")["event"], "need_id")
        tr = Tracker(a)
        self.assertEqual(self.feed(tr, "Avenida Alameda", 41)["room_id"], 41)   # ID visible

    def test_id_on_screen_wins_and_new_rooms_wait_for_approval(self):
        a = new_atlas()
        tr = Tracker(a)
        ev = self.feed(tr, "Sala Nueva", 7777)
        self.assertTrue(ev["new_room"])
        self.assertEqual(a.pending()[0]["room_id"], 7777)


class DatabaseTests(unittest.TestCase):
    def test_import_of_calibration_data(self):
        a = new_atlas()
        st = a.stats()
        self.assertGreaterEqual(st["rooms"], 74)
        self.assertEqual((st["markers"], st["markers_linked"]), (53, 52))
        self.assertEqual(a.room(12411)["name"], "Gran Casino")
        self.assertEqual(a.room(76)["kind"], "player")
        self.assertEqual(a.room(29)["kind"], "street")
        self.assertIsNone(a.import_input())                     # only once

    def test_new_room_waits_for_approval_and_edit_keeps_the_original(self):
        a = new_atlas()
        a.record_visit(41, "Avenida Alameda", None, None, "start")
        r = a.record_visit(5000, "Sala Nueva", "HabboSK", 41, "walk")
        self.assertTrue(r["new_room"])
        [p] = a.pending()
        self.assertEqual(a.connections(5000)[0]["status"], "awaiting approval")
        self.assertEqual(a.accept(p["id"], 4, "X"), "duplicate")    # 4 is Supermercado Eleven
        self.assertEqual(a.accept(p["id"], 5001, "Sala Nueva 2"), "ok")
        self.assertIsNone(a.room(5000))
        self.assertEqual(a.room(5001)["name"], "Sala Nueva 2")
        self.assertEqual(a.connections(5001)[0]["status"], "observed")
        exp = os.path.join(tempfile.mkdtemp(), "x.json")
        a.export(exp)
        data = json.load(open(exp, encoding="utf-8"))
        self.assertEqual(json.loads(data["corrections"][0]["detected"])["room_id"], 5000)

    def test_declined_room_leaves_the_connection_unverified(self):
        a = new_atlas()
        a.record_visit(41, "Avenida Alameda", None, None, "start")
        a.record_visit(5000, "Sala Nueva", None, 41, "walk")
        a.decline(a.pending()[0]["id"])
        self.assertIsNone(a.room(5000))
        self.assertEqual(a.connections(41)[-1]["status"], "unverified")

    def test_rename_keeps_old_name_and_connections_are_one_way(self):
        a = new_atlas()
        a.add_room(13, "Ayuntamiento Nuevo")
        self.assertIn("Ayuntamiento de Ensenada", a.room(13)["aliases"])
        a.record_visit(13, "Ayuntamiento Nuevo", None, None, "start")
        a.record_visit(41, "Avenida Alameda", None, 13, "walk")
        walks = [(c["from_room"], c["to_room"]) for c in a.connections() if c["method"] == "walk"]
        self.assertIn((13, 41), walks)
        self.assertNotIn((41, 13), walks)                        # never assumed both ways

    def test_map_facts_for_the_ai(self):
        a = new_atlas()
        facts = a.facts_for("¿Qué hay al lado del Gran Casino? Spa / Banco")
        self.assertTrue(any("Gran Casino" in f and "nearest on the map" in f for f in facts))
        self.assertEqual(len([f for f in facts if "Gran Casino" in f]), 1)   # not repeated
        self.assertEqual(a.facts_for("¿Cuánto es 7 x 8?"), [])

    def test_kinds(self):
        self.assertEqual(guess_kind(1900003897, "Mina · Nivel 1", "SK"), "generated")
        self.assertEqual(guess_kind(35, "Ct. Concourse", "HabboSK"), "street")
        self.assertEqual(guess_kind(2, "Hospital", "HabboSK"), "place")


if __name__ == "__main__":
    unittest.main()
