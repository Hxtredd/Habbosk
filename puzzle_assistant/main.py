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
    from .detector import TitleDetector
    from .ocr import Tesseract
    from .vision import find_cards, find_panel, to_gray

    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        print(f"Cannot read image: {path}")
        return 2
    ocr = Tesseract(cfg)
    analyzer = Analyzer(cfg, ocr)
    gray = to_gray(img)
    panel = img
    title = TitleDetector(cfg, ocr).discover(gray)
    if title is None:
        print("Title 'Montar un arma' not found - analysing the whole image as the puzzle panel.")
    else:
        print(f"Title found at {title}")
        cands = find_panel(gray, title)
        chosen = cands[0] if len(cands) > 2 else cands[-1]
        for r in cands:
            x, y, w, h = r
            if find_cards(gray[y:y + h, x:x + w], cfg.expected_pieces):
                chosen = r
                break
        x, y, w, h = chosen
        panel = img[y:y + h, x:x + w]
        print(f"Panel: {chosen}")
    a = analyzer.analyze_panel(panel)
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

    from .ui import App
    App(cfg).run()
