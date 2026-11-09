"""Synthetic PDFs for the B6 PDF tests - standard library only, exactly known text.

    data = make([page, page, ...], **options)
    page = Page(ops=[text(72, 700, "Mira maintains the Lantern Garden."), ...])

Fonts on every page (resources):
  /F1  Courier, WinAnsiEncoding, explicit /Widths (600 each) - exact positions
  /F2  Type0 Identity-H with a ToUnicode map (two-byte codes = Unicode code points)
  /F3  TrueType, no Encoding and no ToUnicode (characters cannot be known)
  /F4  Type3 (glyphs are drawings)
  /F5  Courier with /Differences glyph names (code 65..: /M /i /r /a ...)
  /F6  Type0 Identity-H WITHOUT ToUnicode
  /F7  Courier with a ToUnicode map that LIES (shows one text, says another)
Synthetic data only."""
from __future__ import annotations

import zlib

CHAR_W = 600            # Courier: every glyph 600/1000 em


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text(x, y, s, size=12, font="F1", pre=""):
    """One line of text at (x, y)."""
    if font in ("F2", "F6"):
        hexs = "".join("%04X" % ord(c) for c in s)
        return f"BT {pre} /{font} {size} Tf {x} {y} Td <{hexs}> Tj ET\n"
    body = s.encode("cp1252").decode("latin-1")
    return f"BT {pre} /{font} {size} Tf {x} {y} Td ({esc(body)}) Tj ET\n"


def lines(x, y, rows, size=12, leading=None, font="F1"):
    """Several lines, one under the other."""
    leading = leading or size * 1.2
    return "".join(text(x, round(y - i * leading, 2), r, size, font) for i, r in enumerate(rows))


def width(s, size=12):
    return len(s) * CHAR_W / 1000.0 * size


def image(x, y, w, h, name="Im1"):
    return f"q {w} 0 0 {h} {x} {y} cm /{name} Do Q\n"


class Page:
    def __init__(self, ops="", box=(0, 0, 612, 792), crop=None, rotate=0, extra=""):
        self.ops = ops if isinstance(ops, str) else "".join(ops)
        self.box, self.crop, self.rotate, self.extra = box, crop, rotate, extra


def _font_objs(start):
    """(objects dict, resource string) for the seven fonts, numbered from `start`."""
    o = {}
    n = start
    widths = "[" + " ".join(["600"] * 224) + "]"
    o[n] = ("<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding "
            f"/FirstChar 32 /LastChar 255 /Widths {widths} >>")
    f1 = n
    # F2: Type0 Identity-H + ToUnicode (code = Unicode BMP code point)
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            b"/CMapName /SK-UCS def 1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"1 beginbfrange <0020> <D7FF> <0020> endbfrange endcmap CMapName currentdict "
            b"/CMap defineresource pop end end")
    o[n + 1] = ("<< /Length %d >>" % len(cmap), cmap)
    o[n + 2] = ("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SKSans /CIDSystemInfo "
                "<< /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 600 >>")
    o[n + 3] = (f"<< /Type /Font /Subtype /Type0 /BaseFont /SKSans /Encoding /Identity-H "
                f"/DescendantFonts [{n + 2} 0 R] /ToUnicode {n + 1} 0 R >>")
    f2 = n + 3
    o[n + 4] = ("<< /Type /FontDescriptor /FontName /SKSym /Flags 4 /FontBBox [0 0 600 800] "
                "/ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>")
    o[n + 5] = (f"<< /Type /Font /Subtype /TrueType /BaseFont /SKSym /FirstChar 32 /LastChar 255 "
                f"/Widths {widths} /FontDescriptor {n + 4} 0 R >>")
    f3 = n + 5
    o[n + 6] = ("<< /Type /Font /Subtype /Type3 /FontBBox [0 0 600 800] /FontMatrix "
                "[0.001 0 0 0.001 0 0] /CharProcs << >> /Encoding << /Type /Encoding "
                "/Differences [65 /A] >> /FirstChar 65 /LastChar 65 /Widths [600] "
                "/Resources << >> >>")
    f4 = n + 6
    diffs = "[65 /M /i /r /a /space /m /n /t /s /h /e /L /G /d /period]"
    o[n + 7] = ("<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding << /Type /Encoding "
                f"/BaseEncoding /WinAnsiEncoding /Differences {diffs} >> /FirstChar 32 "
                f"/LastChar 255 /Widths {widths} >>")
    f5 = n + 7
    o[n + 8] = (f"<< /Type /Font /Subtype /Type0 /BaseFont /SKSans /Encoding /Identity-H "
                f"/DescendantFonts [{n + 2} 0 R] >>")
    f6 = n + 8
    # F7: WinAnsi Courier whose ToUnicode says 'K' 'a' 'i' for the codes of 'M' 'i' 'r' 'a'
    lie = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
           b"1 begincodespacerange <00> <FF> endcodespacerange "
           b"2 beginbfchar <4D> <004B> <72> <0069> endbfchar endcmap end end")
    o[n + 9] = ("<< /Length %d >>" % len(lie), lie)
    o[n + 10] = ("<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding "
                 f"/FirstChar 32 /LastChar 255 /Widths {widths} /ToUnicode {n + 9} 0 R >>")
    f7 = n + 10
    res = (f"/Font << /F1 {f1} 0 R /F2 {f2} 0 R /F3 {f3} 0 R /F4 {f4} 0 R /F5 {f5} 0 R "
           f"/F6 {f6} 0 R /F7 {f7} 0 R >>")
    return o, res, n + 11


def make(pages, compress=True, image_obj=True, extgstate="", colorspace="", properties="",
         trailer_extra="", catalog_extra="", tail=b"", version="1.7"):
    objs = {}
    fonts, font_res, n = _font_objs(3)
    objs.update(fonts)
    xobj = ""
    if image_obj:
        img = bytes([128]) * 16
        objs[n] = ("<< /Type /XObject /Subtype /Image /Width 4 /Height 4 /ColorSpace "
                   "/DeviceGray /BitsPerComponent 8 /Length %d >>" % len(img), img)
        form = b"BT /F1 12 Tf 5 20 Td (Form text.) Tj ET"
        objs[n + 1] = (f"<< /Type /XObject /Subtype /Form /BBox [0 0 300 50] "
                       f"/Resources << {font_res} >> /Length {len(form)} >>", form)
        xobj = f"/XObject << /Im1 {n} 0 R /Fm1 {n + 1} 0 R >>"
        n += 2
    res = (f"<< {font_res} {xobj} {('/ExtGState << ' + extgstate + ' >>') if extgstate else ''} "
           f"{('/ColorSpace << ' + colorspace + ' >>') if colorspace else ''} "
           f"{('/Properties << ' + properties + ' >>') if properties else ''} >>")
    kids = []
    for p in pages:
        data = p.ops.encode("latin-1")
        if compress:
            data = zlib.compress(data)
            objs[n] = (f"<< /Length {len(data)} /Filter /FlateDecode >>", data)
        else:
            objs[n] = (f"<< /Length {len(data)} >>", data)
        box = " ".join(str(v) for v in p.box)
        crop = f"/CropBox [{' '.join(str(v) for v in p.crop)}]" if p.crop else ""
        rot = f"/Rotate {p.rotate}" if p.rotate else ""
        objs[n + 1] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [{box}] {crop} {rot} "
                       f"/Resources {res} /Contents {n} 0 R {p.extra} >>")
        kids.append(f"{n + 1} 0 R")
        n += 2
    objs[1] = f"<< /Type /Catalog /Pages 2 0 R {catalog_extra} >>"
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(f"%PDF-{version}\n%\xe2\xe3\xcf\xd3\n".encode("latin-1"))
    offs = {}
    for i in sorted(objs):
        offs[i] = len(out)
        ob = objs[i]
        if isinstance(ob, tuple):
            out += f"{i} 0 obj\n{ob[0]}\nstream\n".encode("latin-1") + ob[1] + \
                b"\nendstream\nendobj\n"
        else:
            out += f"{i} 0 obj\n{ob}\nendobj\n".encode("latin-1")
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for i in range(1, size):
        out += (f"{offs[i]:010d} 00000 n \n" if i in offs else "0000000000 65535 f \n").encode()
    out += (f"trailer\n<< /Size {size} /Root 1 0 R {trailer_extra} >>\nstartxref\n{xref}\n"
            f"%%EOF\n").encode()
    return bytes(out) + tail


def simple(paragraphs_per_page, size=12, x=72, top=700, leading=None, gap=30):
    """Pages of single-column paragraphs (each paragraph a list of lines)."""
    leading = leading or size * 1.2
    pages = []
    for paras in paragraphs_per_page:
        ops = []
        y = top
        for para in paras:
            ops.append(lines(x, y, para, size, leading))
            y -= leading * len(para) + gap
        pages.append(Page(ops))
    return make(pages)
