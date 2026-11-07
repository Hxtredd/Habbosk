"""Entry point.

    python app.py                 -> floating window, live screen monitoring
    python app.py --image X.png   -> analyse a saved screenshot and print the result
"""
from __future__ import annotations

import argparse
import sys

from .capture import enable_dpi_awareness
from .config import Config


def analyse_image(path: str, cfg: Config) -> int:
    import cv2

    from .analyzer import Analyzer, describe
    from .ocr import Tesseract
    from .locate import search
    from .vision import to_gray

    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        print(f"Cannot read image: {path}")
        return 2
    ocr = Tesseract(cfg)
    analyzer = Analyzer(cfg, ocr)
    gray = to_gray(img)
    found = search(img, gray, cfg)
    if found is None:
        print("No puzzle card grid found - analysing the whole image as the panel.")
        panel = img
        a = analyzer.analyze_panel(img)
    else:
        (x, y, w, h), cards = found
        print(f"Puzzle panel: {(x, y, w, h)}  pieces: {len(cards)}")
        panel = img[y:y + h, x:x + w]
        a = analyzer.analyze_panel(panel, cards)
    print(describe(a))
    print("Debug image:", analyzer.save_debug(panel, a))
    return 0 if a.solution and a.solution.ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only answer assistant for 'Montar un arma'.")
    ap.add_argument("--image", help="analyse a screenshot file instead of the live screen")
    ap.add_argument("--debug", action="store_true", help="save every analysed frame to ./debug")
    args = ap.parse_args()

    enable_dpi_awareness()  # must run before any window / capture is created
    cfg = Config.load()
    if args.debug:
        cfg.debug_save = True
    if args.image:
        sys.exit(analyse_image(args.image, cfg))

    if not _single_instance():
        return  # already running (e.g. taskbar icon clicked twice)
    from .ui import App
    App(cfg).run()


_MUTEX = None


def _single_instance() -> bool:
    """Windows: allow one running window only."""
    global _MUTEX
    if sys.platform != "win32":
        return True
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "PuzzleAnswerAssistant.Single")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # bring the existing window to the front instead of opening a second one
        hwnd = ctypes.windll.user32.FindWindowW(None, "SK Assistant")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        return False
    return True
