"""Screen capture (read-only) with correct Windows DPI handling."""
from __future__ import annotations

import sys
from typing import Dict, List, Optional

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


def display_info() -> str:
    """Screen layout + DPI mode, written into snapshots for diagnosis."""
    lines = []
    try:
        with new_mss() as sct:
            for i, m in enumerate(sct.monitors):
                lines.append(f"monitor {i}: left={m['left']} top={m['top']} "
                             f"{m['width']}x{m['height']}" + (" (all)" if i == 0 else ""))
    except Exception as exc:
        lines.append(f"monitors: {exc}")
    if sys.platform == "win32":
        import ctypes
        try:
            u = ctypes.windll.user32
            u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            aw = u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())
            lines.append(f"dpi awareness: {aw} (2 = per-monitor, expected)")
        except Exception as exc:
            lines.append(f"dpi awareness: {exc}")
    return "\n".join(lines) + "\n"


def new_mss():
    """mss >= 10 renamed the factory to mss.MSS; support both."""
    import mss
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


class ScreenGrabber:
    """mss is not thread-safe across threads, so create one per worker thread."""

    def __init__(self, monitor_index: int = 0):
        """monitor_index 0 = every monitor (searched one by one), N = only monitor N."""
        self._sct = new_mss()
        mons = self._sct.monitors
        self.screens: List[Dict[str, int]] = [dict(m) for m in mons[1:]] or [dict(mons[0])]
        if 1 <= monitor_index <= len(self.screens):
            self.screens = [self.screens[monitor_index - 1]]
        self.monitor: Dict[str, int] = self.screens[0]

    def targets(self, region: Optional[list]) -> List[Dict[str, int]]:
        """Screen areas to search: the user's region, or every monitor."""
        return [self.bounds(region)] if region else list(self.screens)

    def bounds(self, region: Optional[list]) -> Dict[str, int]:
        if region:
            l, t, w, h = (int(v) for v in region)
            return {"left": l, "top": t, "width": max(1, w), "height": max(1, h)}
        return self.monitor

    def grab(self, region: Optional[list] = None):
        """Return (BGR image, bounds dict). Only reads pixels; never sends input."""
        return self.grab_bounds(self.bounds(region))

    def grab_bounds(self, b: Dict[str, int]):
        shot = self._sct.grab(b)
        img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return np.ascontiguousarray(img[:, :, :3]), b

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
