"""Anti-aliased drawing (Pillow) shared by the on-screen badges and the window.

Everything is drawn at 3x and scaled down, which gives smooth edges, soft
shadows and real transparency (the on-screen badges use per-pixel alpha).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 3  # supersampling factor
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(HERE, "assets", "fonts")

# palette (modern dark UI)
BG = (13, 15, 20)
SURFACE = (22, 25, 33)
SURFACE_2 = (30, 34, 45)
BORDER = (45, 51, 66)
TEXT = (236, 239, 245)
MUTED = (140, 148, 166)
GREEN = (16, 185, 129)
GREEN_SOFT = (52, 211, 153)
BLUE = (96, 165, 250)
RED = (244, 63, 94)
AMBER = (251, 191, 36)


def hexc(c: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


@lru_cache(maxsize=64)
def font(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    for path in (os.path.join(FONT_DIR, f"Inter-{weight}.otf"),
                 "C:/Windows/Fonts/segoeuib.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, max(6, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _shadow(img: Image.Image, shape_fn, offset: Tuple[int, int], blur: float, alpha: int) -> None:
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shape_fn(ImageDraw.Draw(sh), offset, (0, 0, 0, alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(sh)


# ---------------------------------------------------------------------------
# On-screen badges (overlay)
# ---------------------------------------------------------------------------
BADGE_STYLE = {
    #           fill (RGBA)            ring                    text
    "next":  ((16, 185, 129, 245), (167, 243, 208, 255), (255, 255, 255, 255)),
    "step":  ((17, 24, 39, 230),   (96, 165, 250, 255),  (255, 255, 255, 255)),
    "done":  ((16, 185, 129, 200), (16, 185, 129, 0),    (255, 255, 255, 255)),
    "skip":  ((190, 18, 60, 215),  (190, 18, 60, 0),     (255, 255, 255, 255)),
    "doubt": ((217, 119, 6, 235),  (253, 230, 138, 255), (255, 255, 255, 255)),
}


@lru_cache(maxsize=256)
def badge(kind: str, text: str, r: int) -> Image.Image:
    """Round badge with a soft shadow; 'next' also gets a glow halo."""
    fill, ring, tcol = BADGE_STYLE[kind]
    pad = int(r * 0.9) + 4
    size = (2 * (r + pad)) * SS
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = size // 2
    R = r * SS

    def disc(d, off, col, rad=R):
        d.ellipse([c - rad + off[0], c - rad + off[1], c + rad + off[0], c + rad + off[1]], fill=col)

    if kind == "next":  # glow
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        disc(ImageDraw.Draw(glow), (0, 0), (16, 185, 129, 150), int(R * 1.45))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(R * 0.35)))
    _shadow(img, disc, (0, int(R * 0.12)), R * 0.18, 140)
    d = ImageDraw.Draw(img)
    disc(d, (0, 0), fill)
    if ring[3]:
        w = max(SS, int(R * 0.11))
        d.ellipse([c - R, c - R, c + R, c + R], outline=ring, width=w)
    if kind in ("done", "skip"):
        _icon(d, kind, c, R * 0.42, tcol)
    else:
        f = font(R * (1.05 if len(text) == 1 else 0.85), "Bold")
        d.text((c, c + R * 0.02), text, font=f, fill=tcol, anchor="mm")
    out = img.resize((size // SS, size // SS), Image.LANCZOS)
    return out


def _icon(d: ImageDraw.ImageDraw, kind: str, c: int, s: float, col) -> None:
    w = max(SS * 2, int(s * 0.38))
    if kind == "done":   # check mark
        pts = [(c - s, c + s * 0.05), (c - s * 0.3, c + s * 0.72), (c + s, c - s * 0.6)]
        d.line(pts, fill=col, width=w, joint="curve")
    else:                # cross
        d.line([(c - s * 0.8, c - s * 0.8), (c + s * 0.8, c + s * 0.8)], fill=col, width=w)
        d.line([(c - s * 0.8, c + s * 0.8), (c + s * 0.8, c - s * 0.8)], fill=col, width=w)


@lru_cache(maxsize=32)
def pill(text: str, h: int) -> Image.Image:
    """'PROBAR ▸' call-to-action pill (amber), with shadow."""
    f = font(h * 0.46 * SS, "Bold")
    tw = int(f.getlength(text))
    H = h * SS
    W = tw + int(H * 1.5)
    pad = int(H * 0.5)
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))

    def body(d, off, col):
        d.rounded_rectangle([pad + off[0], pad + off[1], pad + W + off[0], pad + H + off[1]],
                            radius=H // 2, fill=col)

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    body(ImageDraw.Draw(glow), (0, 0), (251, 191, 36, 140))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(H * 0.25)))
    _shadow(img, body, (0, int(H * 0.08)), H * 0.12, 150)
    d = ImageDraw.Draw(img)
    body(d, (0, 0), (251, 191, 36, 250))
    ink = (24, 18, 4, 255)
    tri = H * 0.16
    tx = pad + W // 2 - tri
    d.text((tx, pad + H // 2), text, font=f, fill=ink, anchor="mm")
    ax = tx + tw / 2 + tri * 1.3       # play-arrow drawn as a shape
    cy = pad + H / 2
    d.polygon([(ax, cy - tri), (ax + tri * 1.5, cy), (ax, cy + tri)], fill=ink)
    return img.resize((img.width // SS, img.height // SS), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Window: step chips
# ---------------------------------------------------------------------------
def chips(steps: Sequence[Tuple[str, int, str, str]], width: int, bg=SURFACE,
          max_chip: int = 64) -> Image.Image:
    """steps: (letter, number, state, name) with state in next/step/done.
    Lays the chips out in rows that fit `width`."""
    n = max(1, len(steps))
    gap = 8
    cw = int(min(max_chip, max(40, (width - gap * (n - 1)) / n)))
    per_row = max(1, (width + gap) // (cw + gap))
    rows = (n + per_row - 1) // per_row
    ch = int(cw * 1.12)
    W, H = width, rows * ch + (rows - 1) * gap
    img = Image.new("RGBA", (W * SS, H * SS), bg + (255,))
    for i, (letter, num, state, _name) in enumerate(steps):
        r, col = divmod(i, per_row)
        x0, y0 = col * (cw + gap) * SS, r * (ch + gap) * SS
        _chip(img, (x0, y0, x0 + cw * SS, y0 + ch * SS), letter, num, state)
    return img.resize((W, H), Image.LANCZOS)


def _chip(img: Image.Image, box, letter: str, num: int, state: str) -> None:
    x0, y0, x1, y1 = box
    w = x1 - x0
    rad = int(w * 0.22)
    d = ImageDraw.Draw(img)
    if state == "next":
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(box, radius=rad, fill=(16, 185, 129, 110))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(w * 0.08)))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(box, radius=rad, fill=GREEN + (255,))
        num_col, let_col = (209, 250, 229, 255), (255, 255, 255, 255)
    elif state == "done":
        d.rounded_rectangle(box, radius=rad, fill=SURFACE_2 + (255,))
        num_col, let_col = (90, 98, 116, 255), (90, 98, 116, 255)
    else:
        d.rounded_rectangle(box, radius=rad, fill=SURFACE_2 + (255,),
                            outline=(59, 130, 246, 255), width=max(2, w // 40))
        num_col, let_col = BLUE + (255,), TEXT + (255,)
    d.text((x0 + w * 0.16, y0 + w * 0.12), str(num), font=font(w * 0.24, "SemiBold"),
           fill=num_col, anchor="lt")
    d.text(((x0 + x1) / 2, y0 + (y1 - y0) * 0.58), letter, font=font(w * 0.52, "Bold"),
           fill=let_col, anchor="mm")
    if state == "done":
        cx, cy, s = x1 - w * 0.2, y0 + w * 0.2, w * 0.09
        d.line([(cx - s, cy), (cx - s * 0.3, cy + s * 0.7), (cx + s, cy - s * 0.6)],
               fill=GREEN + (255,), width=max(2, int(w * 0.05)), joint="curve")


def tags(items: Sequence[Tuple[str, str]], height: int, colour, bg=SURFACE) -> Optional[Image.Image]:
    """Small outlined tags, e.g. [D · 9] [E · 45] for 'don't click'."""
    if not items:
        return None
    f = font(height * 0.5 * SS, "SemiBold")
    H = height * SS
    widths = [int(f.getlength(f"{a}  ·  {b}")) + H for a, b in items]
    gap = 6 * SS
    W = sum(widths) + gap * (len(items) - 1)
    img = Image.new("RGBA", (W, H), bg + (255,))
    d = ImageDraw.Draw(img)
    x = 0
    for (a, b), w in zip(items, widths):
        d.rounded_rectangle([x, 0, x + w - 1, H - 1], radius=H // 2,
                            fill=tuple(int(v * 0.18 + bgv * 0.82) for v, bgv in zip(colour, bg)) + (255,),
                            outline=colour + (255,), width=SS * 2)
        d.text((x + w / 2, H / 2), f"{a}  ·  {b}", font=f, fill=colour + (255,), anchor="mm")
        x += w + gap
    return img.resize((W // SS, H // SS), Image.LANCZOS)


def status_pill(text: str, colour, height: int = 22, bg=BG) -> Image.Image:
    f = font(height * 0.5 * SS, "SemiBold")
    H = height * SS
    W = int(f.getlength(text)) + int(H * 1.6)
    img = Image.new("RGBA", (W, H), bg + (255,))
    d = ImageDraw.Draw(img)
    soft = tuple(int(v * 0.2 + b * 0.8) for v, b in zip(colour, bg))
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=soft + (255,))
    dot = H * 0.18
    d.ellipse([H * 0.45 - dot, H / 2 - dot, H * 0.45 + dot, H / 2 + dot], fill=colour + (255,))
    d.text((H * 0.75, H / 2), text, font=f, fill=colour + (255,), anchor="lm")
    return img.resize((W // SS, H // SS), Image.LANCZOS)


TOTAL_STYLE = {
    #                fill                  ring                  text
    "total_ok":     ((16, 185, 129, 245), (167, 243, 208, 255), (255, 255, 255, 255)),
    "total_count":  ((17, 24, 39, 235),   (96, 165, 250, 255),  (191, 219, 254, 255)),
    "total_unsure": ((217, 119, 6, 240),  (253, 230, 138, 255), (255, 255, 255, 255)),
}


@lru_cache(maxsize=64)
def total_badge(kind: str, text: str, h: int) -> Image.Image:
    """Rounded badge for the money total, e.g. '= 390'."""
    fill, ring, tcol = TOTAL_STYLE[kind]
    f = font(h * 0.55 * SS, "Bold")
    H = h * SS
    W = int(f.getlength(text)) + int(H * 0.9)
    pad = int(H * 0.5)
    img = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))

    def body(d, off, col):
        d.rounded_rectangle([pad + off[0], pad + off[1], pad + W + off[0], pad + H + off[1]],
                            radius=H // 2, fill=col)

    if kind == "total_ok":
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        body(ImageDraw.Draw(glow), (0, 0), (16, 185, 129, 140))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(H * 0.22)))
    _shadow(img, body, (0, int(H * 0.08)), H * 0.12, 150)
    d = ImageDraw.Draw(img)
    body(d, (0, 0), fill)
    d.rounded_rectangle([pad, pad, pad + W, pad + H], radius=H // 2, outline=ring,
                        width=max(SS, int(H * 0.06)))
    d.text((pad + W / 2, pad + H / 2), text, font=f, fill=tcol, anchor="mm")
    return img.resize((img.width // SS, img.height // SS), Image.LANCZOS)


def compose_overlay(items, scale: float = 1.0) -> Tuple[Optional[Image.Image], int, int]:
    """All overlay items in one transparent image + its screen position."""
    sprites: List[Tuple[Image.Image, int, int]] = []
    for it in items:
        if it.kind == "probar":
            im = pill("PROBAR", max(18, int(it.r * 2.0)))
        elif it.kind.startswith("total"):
            im = total_badge(it.kind, it.text, max(16, int(it.r * 2)))
        else:
            im = badge(it.kind, it.text, it.r)
        sprites.append((im, it.x - im.width // 2, it.y - im.height // 2))
    if not sprites:
        return None, 0, 0
    x0 = min(x for _, x, _ in sprites)
    y0 = min(y for _, _, y in sprites)
    x1 = max(x + im.width for im, x, _ in sprites)
    y1 = max(y + im.height for im, _, y in sprites)
    canvas = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    for im, x, y in sprites:
        canvas.alpha_composite(im, (x - x0, y - y0))
    return canvas, x0, y0
