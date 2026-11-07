"""Builds puzzle_assistant/glyph_templates.npz (bundled; run only to regenerate).

Renders A-H and 0-9 from several monospace / sans fonts at many pixel sizes.
The game's small monospace text (calibers, dependency letters) is matched
against these shapes, which is far more reliable than OCR for 5x7-pixel glyphs.
"""
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from puzzle_assistant.glyphs import normalize  # noqa: E402

CHARS = "ABCDEFGH0123456789"
PATTERNS = ["/usr/share/fonts/truetype/**/*Mono*.ttf", "/usr/share/fonts/truetype/**/*mono*.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans*.ttf",
            "C:/Windows/Fonts/consola*.ttf", "C:/Windows/Fonts/CascadiaMono*.ttf",
            "C:/Windows/Fonts/cour*.ttf", "C:/Windows/Fonts/lucon.ttf"]


def main():
    fonts = sorted({f for p in PATTERNS for f in glob.glob(p, recursive=True)
                    if "Italic" not in f and "Oblique" not in f})
    labels, tmpls = [], []
    for f in fonts:
        for size in range(7, 19):
            fo = ImageFont.truetype(f, size)
            for ch in CHARS:
                im = Image.new("L", (size * 2, size * 2), 0)
                ImageDraw.Draw(im).text((size // 2, size // 4), ch, fill=255, font=fo)
                t = normalize(np.array(im))
                if t is not None:
                    labels.append(ch)
                    tmpls.append(t.astype(np.float16))
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "puzzle_assistant", "glyph_templates.npz")
    np.savez_compressed(out, labels=np.array(labels), templates=np.stack(tmpls))
    print(f"{len(fonts)} fonts, {len(labels)} templates -> {out}")


if __name__ == "__main__":
    main()
