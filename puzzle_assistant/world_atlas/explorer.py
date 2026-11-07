"""Live Explorer: watches the screen (read-only) and keeps the World Atlas up
to date as the player moves through the hotel.

The tracker is separate from the screen grabbing so it can be tested with
recorded frames. Rules:
  * a room change counts only after the new reading is stable (2 checks);
  * walking shows a black flash / grey loading screen: the whole room area
    turns flat and, within ~2 s, changes again into the new room. Only that
    sequence counts as walking (some rooms have plain backgrounds as flat as
    the loading screen, so flatness alone is not enough). A walk records an
    observed one-way connection previous -> new room;
  * an instant change (no loading screen) is normally a taxi trip: it is a
    visit, not a walking route, so no connection is stored for it;
  * without a room ID (share panel closed) nothing is recorded: the window
    asks for the share panel instead. Names alone never identify a room.
"""
from __future__ import annotations

import difflib
import queue
import threading
import time
from typing import Optional

import numpy as np

from .recognize import RoomView, flatness, read_view, thumb


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return True
    a, b = a.lower().replace(" ", ""), b.lower().replace(" ", "")
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


class Tracker:
    STABLE = 2

    def __init__(self, atlas):
        self.atlas = atlas
        self.current: Optional[dict] = None      # {"room_id", "name"}
        self._cand = None
        self._count = 0
        self._loading_since_room = False
        self._rename = (None, 0)
        self._prev_thumb = None
        self._flat_at = -1e9
        self._t = 0.0
        self.state = "Starting…"
        self.last_change = ""
        self.need_id = False

    MOVE = 20          # mean grey difference that counts as "the scene changed"
    FLAT = 0.7

    def motion(self, t_img: np.ndarray, t: float) -> bool:
        """Cheap check of every frame. Returns True when the scene changed
        (then a full reading is worth doing)."""
        self._t = t
        prev, self._prev_thumb = self._prev_thumb, t_img
        if prev is None:
            return True
        diff = float(np.mean(np.abs(t_img.astype(np.int16) - prev.astype(np.int16))))
        if diff < self.MOVE:
            return False
        if flatness(t_img) >= self.FLAT:
            self._flat_at = t                    # maybe a loading screen...
            self.state = "Changing room (loading)…"
        elif t - self._flat_at <= 2.5:
            self._loading_since_room = True      # ...and then a new scene: walking
            self._flat_at = -1e9
        return True

    def _undecided(self) -> bool:
        """A flat screen appeared moments ago: it may be a loading screen
        (walking) or a plain room (taxi). Wait until that is clear."""
        return self._t - self._flat_at <= 2.5

    def feed(self, v: RoomView) -> Optional[dict]:
        """One reading of the screen. Returns an event dict when something
        was recorded, else None.

        The room is identified by its NAME (bar above the chat), matched to
        the rooms the Atlas knows; the share panel's ID, when visible, wins
        and also teaches new names. A name SK doesn't know (or one shared by
        several rooms) without a visible ID is asked to the owner."""
        if self._undecided():
            return None
        if not v.in_room:
            self.state = "Not in a room view (map, menu or something covers the name bar)"
            return None
        if not v.name:
            return None
        if self._cand is not None and _same_name(v.name, self._cand[1]):
            self._count += 1
            if v.room_id is not None:
                self._cand = (v.room_id, self._cand[1])
        else:
            self._cand, self._count = (v.room_id, v.name), 1
        if self._count < self.STABLE:
            return None
        rid, name = self._cand
        cur = self.current
        how = "id"
        if rid is None:
            found = self.atlas.find_by_name(name)
            if len(found) == 1:
                rid, how = found[0]["room_id"], "name"
        if cur is not None and rid is not None and cur.get("room_id") == rid:
            self.state = "In room"
            self.need_id = False
            self._loading_since_room = False     # a flash without a room change
            # same ID, different name: a rename (only if seen several times)
            if how == "id" and not _same_name(name, cur["name"]):
                key, n = self._rename
                n = n + 1 if key == name else 1
                self._rename = (name, n)
                if n >= 3:
                    self.atlas.add_room(rid, name)
                    cur["name"] = name
                    self._rename = (None, 0)
                    return {"event": "renamed", "room_id": rid, "name": name}
            return None
        if cur is not None and rid is None and _same_name(name, cur["name"]):
            return None                          # still in the same (unidentified) room
        method = "walk" if (cur is not None and self._loading_since_room) else (
            "taxi" if cur is not None else "start")
        frm = cur.get("room_id") if cur else None
        self._loading_since_room = False
        hows = {"walk": "walked", "taxi": "instant change (taxi)", "start": "first room seen"}
        prev = (f"{cur['name']} ({cur.get('room_id') or '?'}) → " if cur else "")
        if rid is None:
            # unknown name (or several rooms with it) and no ID on screen: ask
            self.current = {"room_id": None, "name": name}
            self.need_id = True
            asked = self.atlas.ask_for_id(name, frm, method)
            self.state = f"New room “{name}” - add its ID in the Explorer (or open the share panel)"
            self.last_change = prev + f"{name} (ID?) · {hows[method]}"
            return {"event": "need_id", "name": name, "from": frm, "method": method, "asked": asked}
        self.need_id = False
        res = self.atlas.record_visit(rid, name, v.owner, frm, method)
        self.current = {"room_id": rid, "name": name}
        self.state = "In room" + (" (recognised by its name)" if how == "name" else "")
        self.last_change = prev + f"{name} ({rid}) · {hows[method]}"
        return {"event": "room", "room_id": rid, "name": name, "from": frm, "method": method,
                "new_room": res["new_room"], "connection": res["connection"], "by": how}


class ExplorerThread(threading.Thread):
    """Grabs the screen about once a second and feeds the tracker."""

    def __init__(self, atlas, ocr_factory, cfg, out: "queue.Queue[dict]"):
        super().__init__(daemon=True, name="world-atlas")
        self.atlas, self.cfg, self.out = atlas, cfg, out
        self._ocr_factory = ocr_factory
        self.tracker = Tracker(atlas)
        self.view: Optional[RoomView] = None
        self._stop = threading.Event()
        self.paused = threading.Event()
        self.engine_busy = None                  # callable: a puzzle / question is on screen

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from ..capture import ScreenGrabber
        try:
            ocr = self._ocr_factory()
        except Exception as exc:                 # no Tesseract: say so, never crash
            self.tracker.state = f"Explorer unavailable: {exc}"
            return
        grabber = ScreenGrabber(self.cfg.monitor)
        cache: dict = {}
        order = list(range(len(grabber.screens)))
        last_read = 0.0
        last_url_search = -1e9
        url_where = None
        last_name = None
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                if (self.paused.is_set() or not getattr(self.cfg, "world_atlas", True)
                        or (self.engine_busy is not None and self.engine_busy())):
                    # a puzzle or a question is on screen: it gets the computer
                    time.sleep(0.5)
                    continue
                img, _b = grabber.grab_bounds(grabber.screens[order[0]])
                moved = self.tracker.motion(thumb(img), t0)
                if moved or t0 - last_read >= 2.0:
                    last_read = t0
                    name_changed = self.tracker._cand is None or (
                        self.view is not None and self.view.name != last_name)
                    search = moved or name_changed or t0 - last_url_search >= 8.0
                    if search:
                        last_url_search = t0
                    v = read_view(img, ocr, cache, want_owner=self._want_owner(),
                                  url_where=url_where, search_url=search)
                    url_where = v.url_box
                    if not v.in_room and len(order) > 1 and not moved:
                        for k in order[1:]:          # the game may be on another screen
                            img2, _b = grabber.grab_bounds(grabber.screens[k])
                            v2 = read_view(img2, ocr, cache, search_url=False)
                            if v2.in_room:
                                order.remove(k)
                                order.insert(0, k)
                                v = v2
                                break
                    last_name = self.view.name if self.view is not None else None
                    self.view = v
                    ev = self.tracker.feed(v)
                    if ev:
                        self.out.put(ev)
                time.sleep(max(0.05, 0.3 - (time.monotonic() - t0)))
        finally:
            grabber.close()

    def _want_owner(self) -> bool:
        # the owner is read only for rooms the Atlas doesn't know yet (OCR cost)
        cand = self.tracker._cand
        return bool(cand and cand[0] is not None and self.atlas.room(cand[0]) is None)
