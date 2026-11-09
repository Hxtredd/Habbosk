"""v3.3: the share panel can still show the PREVIOUS room's address after
walking on. SK used to read "Tienda de Armas" + ID 2 and rename Hospital (2)
to "Tienda de Armas". Now an ID whose known room has another name is not
trusted (the name decides, or SK asks), and the wrong renames already made
are repaired once (recorded as corrections)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402
from puzzle_assistant.world_atlas.explorer import Tracker  # noqa: E402
from puzzle_assistant.world_atlas.recognize import RoomView  # noqa: E402


def new_atlas():
    a = Atlas(os.path.join(tempfile.mkdtemp(), "atlas.db"))
    a.import_input()
    return a


def feed(tr, name, rid=None, n=4):
    ev = None
    for _ in range(n):
        ev = tr.feed(RoomView(name=name, room_id=rid, in_room=True)) or ev
    return ev


class StaleShareLink(unittest.TestCase):
    def test_walking_on_with_the_old_address_still_showing(self):
        a = new_atlas()
        tr = Tracker(a)
        ev = feed(tr, "Hospital", 2)
        self.assertEqual((ev["room_id"], ev["by"]), (2, "id"))
        tr._loading_since_room = True                       # walked through an arrow
        ev = feed(tr, "Tienda de Armas", 2)                 # the panel still says /room/2
        self.assertEqual((ev["event"], ev["room_id"], ev["by"]), ("room", 8, "name"))
        self.assertEqual(tr.current["room_id"], 8)
        self.assertEqual(a.room(2)["name"], "Hospital")     # never renamed
        self.assertNotIn("Tienda de Armas", a.room(2)["aliases"])
        self.assertEqual([r["room_id"] for r in a.find_by_name("Tienda de Armas")], [8])

    def test_an_unknown_name_with_an_old_address_is_asked_not_renamed(self):
        a = new_atlas()
        tr = Tracker(a)
        feed(tr, "Hospital", 2)
        tr._loading_since_room = True
        ev = feed(tr, "Sala Secreta Nueva", 2)
        self.assertEqual(ev["event"], "need_id")
        self.assertEqual(a.room(2)["name"], "Hospital")

    def test_a_matching_id_is_still_used(self):
        a = new_atlas()
        tr = Tracker(a)
        ev = feed(tr, "Tienda de Armas", 8)
        self.assertEqual((ev["room_id"], ev["by"]), (8, "id"))

    def test_a_new_rooms_id_is_learnt_with_its_name(self):
        a = new_atlas()
        tr = Tracker(a)
        ev = feed(tr, "Sala Nueva Del Puerto", 990001)
        self.assertEqual(ev["room_id"], 990001)
        self.assertEqual(a.room(990001)["name"], "Sala Nueva Del Puerto")


class RepairOldRenames(unittest.TestCase):
    def test_a_rename_to_another_rooms_name_is_undone_once(self):
        a = new_atlas()
        a.add_room(2, "Tienda de Armas")                    # what the old explorer did
        self.assertEqual(a.room(2)["aliases"], ["Hospital"])
        fixed = a.repair_stale_renames()
        self.assertEqual(fixed, [{"room_id": 2, "was": "Tienda de Armas", "now": "Hospital"}])
        self.assertEqual((a.room(2)["name"], a.room(2)["aliases"]), ("Hospital", []))
        self.assertEqual(a.room(8)["name"], "Tienda de Armas")     # the real one is untouched
        with a._lock:
            rows = a._db.execute("SELECT room_id, detected, corrected FROM corrections").fetchall()
        self.assertIn((2, "Tienda de Armas", "Hospital"), [tuple(r) for r in rows])
        self.assertEqual(a.repair_stale_renames(), [])      # once only

    def test_a_real_rename_to_a_unique_name_is_kept(self):
        a = new_atlas()
        a.add_room(13, "Ayuntamiento Nuevo")                # a name no other room has
        self.assertEqual(a.repair_stale_renames(), [])
        self.assertEqual(a.room(13)["name"], "Ayuntamiento Nuevo")


if __name__ == "__main__":
    unittest.main()
