"""Screen capture (read-only) with correct Windows DPI handling."""
from __future__ import annotations

import sys
from typing import Dict, Optional

import numpy as np


def enable_dpi_awareness() -> None:
    """Make Windows report real pixels so coordinates match at 125%/150% scaling."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def new_mss():
    """mss >= 10 renamed the factory to mss.MSS; support both."""
    import mss
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


class ScreenGrabber:
    """mss is not thread-safe across threads, so create one per worker thread."""

    def __init__(self, monitor_index: int = 1):
        self._sct = new_mss()
        mons = self._sct.monitors
        if monitor_index < 0 or monitor_index >= len(mons):
            monitor_index = 1 if len(mons) > 1 else 0
        self.monitor: Dict[str, int] = dict(mons[monitor_index])

    def bounds(self, region: Optional[list]) -> Dict[str, int]:
        if region:
            l, t, w, h = (int(v) for v in region)
            return {"left": l, "top": t, "width": max(1, w), "height": max(1, h)}
        return self.monitor

    def grab(self, region: Optional[list] = None):
        """Return (BGR image, bounds dict). Only reads pixels; never sends input."""
        b = self.bounds(region)
        shot = self._sct.grab(b)
        img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(img[:, :, :3]), b

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
