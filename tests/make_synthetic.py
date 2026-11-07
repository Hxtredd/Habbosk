"""Render a synthetic 'Montar un arma' screen for offline pipeline testing.

The real game art is different; this only exercises the code path
(detection -> panel -> cards -> OCR -> parse -> solve).
"""
import sys

from PIL import Image, ImageDraw, ImageFont

PIECES = [  # letter, name, caliber, deps
    ("A", "Culata", None, "H"),
    ("B", "Gatillo", "12", "G"),
    ("C", "Cañón", "12", "H"),
    ("D", "Pasador", None, "C H"),
    ("E", "Empuñadura", None, "G"),
    ("F", "Cañón", "9", "H"),
    ("G", "Armazón", "12", "C"),
    ("H", "Corredera", None, ""),
]


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for base in ("/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/"):
        try:
            return ImageFont.truetype(base + name, size)
        except OSError:
            pass
    return ImageFont.truetype("arial.ttf", size)


def render(path, scale=1.0, pieces=PIECES, target="12"):
    W, H = int(1600 * scale), int(900 * scale)
    s = lambda v: int(v * scale)
    im = Image.new("RGB", (W, H), (40, 60, 80))
    d = ImageDraw.Draw(im)
    for i in range(0, W, s(64)):  # some "game room" clutter
        d.line([(i, 0), (i + s(300), H)], fill=(50, 72, 95), width=s(2))
    px, py, pw, ph = s(380), s(140), s(820), s(600)
    d.rectangle([px, py, px + pw, py + ph], fill=(235, 230, 215), outline=(20, 20, 20), width=s(3))
    d.text((px + s(20), py + s(14)), "Montar un arma", fill=(20, 20, 20), font=font(s(28), True))
    d.text((px + s(20), py + s(54)),
           "Monta cada pieza cuando estén puestas las que indica su flecha.",
           fill=(50, 50, 50), font=font(s(15)))
    d.text((px + s(20), py + s(74)), "Aparta las de otro calibre y prueba el arma al acabar.",
           fill=(50, 50, 50), font=font(s(15)))
    d.text((px + s(20), py + s(104)), f"Pistola · Calibre {target}", fill=(140, 20, 20),
           font=font(s(20), True))
    d.text((px + pw - s(90), py + s(14)), "0:27", fill=(140, 20, 20), font=font(s(26), True))
    cw, ch, gap = s(185), s(200), s(12)
    for i, (letter, name, cal, deps) in enumerate(pieces):
        r, c = divmod(i, 4)
        x = px + s(20) + c * (cw + gap)
        y = py + s(150) + r * (ch + gap)
        d.rectangle([x, y, x + cw, y + ch], fill=(255, 255, 250), outline=(90, 70, 40), width=s(2))
        d.text((x + s(10), y + s(6)), letter, fill=(20, 40, 120), font=font(s(34), True))
        d.text((x + s(10), y + s(56)), name, fill=(20, 20, 20), font=font(s(19)))
        if cal:
            d.text((x + s(10), y + s(90)), f"Cal. {cal}", fill=(120, 30, 30), font=font(s(18), True))
        if deps:
            d.text((x + s(10), y + s(140)), "← " + deps, fill=(20, 90, 20), font=font(s(24), True))
    im.save(path)


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else "synthetic.png",
           float(sys.argv[2]) if len(sys.argv) > 2 else 1.0)
