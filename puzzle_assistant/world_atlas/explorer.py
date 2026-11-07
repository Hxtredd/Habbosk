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
        was recorded, else None."""
        if self._undecided():
            return None
        if not v.in_room:
            self.state = "Not in a room view (map, menu or something covers the name bar)"
            return None
        cand = (v.room_id, v.name)
        if self._cand is not None and cand[0] == self._cand[0] and _same_name(cand[1], self._cand[1]):
            self._count += 1
        else:
            self._cand, self._count = cand, 1
        if self._count < self.STABLE:
            return None
        rid, name = cand
        if rid is None:
            self.need_id = True
            self.state = "In a room - open the share panel so SK can read the room ID"
            if self.current and not _same_name(name, self.current["name"]):
                self.state = f"New room “{name}” - room ID needed: open the share panel"
            return None
        self.need_id = False
        cur = self.current
        if cur is not None and cur["room_id"] == rid:
            self.state = "In room"
            self._loading_since_room = False     # a flash without a room change
            # same room, different name: a rename (only if seen several times)
            if name and not _same_name(name, cur["name"]):
                key, n = self._rename
                n = n + 1 if key == name else 1
                self._rename = (name, n)
                if n >= 3:
                    self.atlas.add_room(rid, name)
                    cur["name"] = name
                    self._rename = (None, 0)
                    return {"event": "renamed", "room_id": rid, "name": name}
            return None
        method = "walk" if (cur is not None and self._loading_since_room) else (
            "taxi" if cur is not None else "start")
        frm = cur["room_id"] if cur else None
        res = self.atlas.record_visit(rid, name or "", v.owner, frm, method)
        self.current = {"room_id": rid, "name": name}
        self._loading_since_room = False
        self.state = "In room"
        how = {"walk": "walked", "taxi": "instant change (taxi)", "start": "first room seen"}[method]
        self.last_change = (f"{cur['name']} ({cur['room_id']}) → " if cur else "") + \
            f"{name} ({rid}) · {how}"
        return {"event": "room", "room_id": rid, "name": name, "from": frm, "method": method,
                "new_room": res["new_room"], "connection": res["connection"]}


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
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                if self.paused.is_set() or not getattr(self.cfg, "world_atlas", True):
                    time.sleep(0.5)
                    continue
                img, _b = grabber.grab_bounds(grabber.screens[order[0]])
                moved = self.tracker.motion(thumb(img), t0)
                if moved or t0 - last_read >= 0.8:
                    last_read = t0
                    v = read_view(img, ocr, cache, want_owner=self._want_owner())
                    if not v.in_room and len(order) > 1:
                        for k in order[1:]:          # the game may be on another screen
                            img2, _b = grabber.grab_bounds(grabber.screens[k])
                            v2 = read_view(img2, ocr, cache)
                            if v2.in_room:
                                order.remove(k)
                                order.insert(0, k)
                                v = v2
                                break
                    self.view = v
                    ev = self.tracker.feed(v)
                    if ev:
                        self.out.put(ev)
                time.sleep(max(0.05, 0.2 - (time.monotonic() - t0)))
        finally:
            grabber.close()

    def _want_owner(self) -> bool:
        # the owner is read only for rooms the Atlas doesn't know yet (OCR cost)
        cand = self.tracker._cand
        return bool(cand and cand[0] is not None and self.atlas.room(cand[0]) is None)
