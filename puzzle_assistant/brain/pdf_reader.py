"""B6 PDF reader - runs ONLY in its own, time-limited process (brain/pdf_import.py
starts it with ``python -I``; the PDF's bytes arrive on stdin, one JSON object
leaves on stdout). Never imported by the Brain itself.

pypdf (pinned, requirements-brain.txt) is used only for the file structure:
strict parsing, encryption detection, stream decoding (with low limits) and
splitting a page's drawing instructions into operators. Everything that
decides what TEXT a page shows - positions, fonts, character codes,
visibility - is interpreted here, conservatively:

  * a character is decoded only from a mapping that is certain (the font's
    ToUnicode map, or a standard encoding / Differences glyph names);
    anything else becomes U+FFFD and flags the page ``pdf_uncertain_text``;
  * text a reader may not see is dropped or flags the page: invisible render
    modes, white / transparent / tiny / rotated / skewed / squeezed text,
    text outside the page or its clipping rectangle, text later painted over,
    complex clipping, optional-content layers, Type3 fonts, unknown colours;
  * invisible text over images is an OCR layer: kept as reference only
    (``pdf_ocr_layer``); a page of images without text is ``image_only``;
  * images are never decoded (no image parser is ever called);
  * fixed limits on pages, operators, runs, characters, forms and maps.
A page with any flag is reference material: its passages never support,
challenge or become suggestions (enforced by the B6 gates on passage flags).
"""
from __future__ import annotations

import json
import logging
import math
import sys

MAX_PAGES = 300
MAX_OPS_PAGE = 400_000
MAX_OPS_TOTAL = 4_000_000
MAX_RUNS_PAGE = 60_000
MAX_CHARS_TOTAL = 8_000_000
MAX_FORM_DEPTH = 6
MAX_FORMS_TOTAL = 5_000
MAX_CMAP_ENTRIES = 70_000
MAX_STREAM = 24 * 1024 * 1024          # decoded bytes of any one stream

BLOCKING = ("pdf_uncertain_text", "pdf_hidden_text", "pdf_tiny_text", "pdf_rotated_text",
            "pdf_scaled_text", "pdf_covered_text", "pdf_complex_clip", "pdf_optional_content",
            "pdf_uncertain_colour", "pdf_type3_font", "pdf_ocr_layer", "pdf_rotated_page",
            "pdf_reader_warning")


class Refuse(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class _Budget:
    def __init__(self):
        self.ops = 0
        self.chars = 0
        self.forms = 0

    def op(self, page_ops: list) -> None:
        self.ops += 1
        page_ops[0] += 1
        if self.ops > MAX_OPS_TOTAL or page_ops[0] > MAX_OPS_PAGE:
            raise Refuse("too_complex", "The PDF is too complex to read safely (too many "
                                        "drawing instructions) - nothing was imported.")


# ------------------------------------------------------------------ matrices
def _mul(a, b):
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _pt(m, x, y):
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


ID = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _num(x) -> float:
    v = float(x)
    if not math.isfinite(v) or abs(v) > 1e7:
        raise ValueError("number out of range")
    return v


def _box(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _inter(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


# ------------------------------------------------------------------ fonts
def _resolve(o):
    seen = 0
    while hasattr(o, "get_object") and seen < 32:
        n = o.get_object()
        if n is o:
            break
        o, seen = n, seen + 1
    return o


def _name(o) -> str:
    o = _resolve(o)
    return str(o) if o is not None else ""


def _data(stream) -> bytes:
    stream = _resolve(stream)
    d = stream.get_data()
    if len(d) > MAX_STREAM:
        raise Refuse("too_complex", "A part of the PDF is too large once decompressed - "
                                    "nothing was imported.")
    return d


def _hexbytes(tok: bytes) -> bytes:
    h = tok.strip(b"<>").replace(b" ", b"").replace(b"\n", b"").replace(b"\r", b"")
    if len(h) % 2:
        h += b"0"
    return bytes.fromhex(h.decode("ascii"))


def _utf16(b: bytes):
    try:
        s = b.decode("utf-16-be")
    except UnicodeDecodeError:
        return None
    return s


def parse_tounicode(data: bytes):
    """{code bytes: text} from a ToUnicode CMap, or None if it cannot be read
    with certainty. Only bfchar / bfrange (both forms)."""
    import re
    toks = re.findall(rb"<[0-9A-Fa-f\s]*>|\[|\]|begincodespacerange|endcodespacerange|"
                      rb"beginbfchar|endbfchar|beginbfrange|endbfrange|"
                      rb"begincidrange|begincidchar|beginnotdefrange", data)
    out = {}
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t in (b"begincidrange", b"begincidchar", b"beginnotdefrange"):
            return None                       # not a ToUnicode map
        if t == b"beginbfchar":
            i += 1
            while i < n and toks[i] != b"endbfchar":
                if i + 1 >= n or not toks[i].startswith(b"<") or not toks[i + 1].startswith(b"<"):
                    return None
                dst = _utf16(_hexbytes(toks[i + 1]))
                if dst is None:
                    return None
                out[_hexbytes(toks[i])] = dst
                i += 2
        elif t == b"beginbfrange":
            i += 1
            while i < n and toks[i] != b"endbfrange":
                if i + 2 >= n:
                    return None
                lo, hi = _hexbytes(toks[i]), _hexbytes(toks[i + 1])
                if len(lo) != len(hi) or not lo:
                    return None
                a, b = int.from_bytes(lo, "big"), int.from_bytes(hi, "big")
                if b < a or b - a > 65535:
                    return None
                if toks[i + 2] == b"[":
                    j = i + 3
                    for code in range(a, b + 1):
                        if j >= n or not toks[j].startswith(b"<"):
                            return None
                        dst = _utf16(_hexbytes(toks[j]))
                        if dst is None:
                            return None
                        out[code.to_bytes(len(lo), "big")] = dst
                        j += 1
                    if j >= n or toks[j] != b"]":
                        return None
                    i = j + 1
                else:
                    base = _hexbytes(toks[i + 2])
                    if len(base) < 2 or len(base) % 2:
                        return None
                    start = int.from_bytes(base, "big")
                    for k, code in enumerate(range(a, b + 1)):
                        val = start + k
                        dst = _utf16(val.to_bytes(len(base), "big"))
                        if dst is not None:            # a lone surrogate: left unmapped
                            out[code.to_bytes(len(lo), "big")] = dst
                    i += 3
                if len(out) > MAX_CMAP_ENTRIES:
                    raise Refuse("too_complex", "A font character map is too large - nothing "
                                                "was imported.")
        i += 1
    return out


def _std_tables():
    try:
        from pypdf._codecs import adobe_glyphs, charset_encoding
        return adobe_glyphs, charset_encoding
    except Exception:                          # pragma: no cover - other pypdf layout
        return {}, {}


def _core_widths(base: str):
    try:
        from pypdf._codecs.core_font_metrics import CORE_FONT_METRICS
    except Exception:                          # pragma: no cover
        return None
    m = CORE_FONT_METRICS.get(base)
    return m.character_widths if m is not None else None


class Font:
    """Decodes a font's character codes - only with certainty."""

    def __init__(self, obj):
        glyphs, encodings = _std_tables()
        f = _resolve(obj)
        self.subtype = _name(f.get("/Subtype"))
        self.base = _name(f.get("/BaseFont")).lstrip("/").split("+")[-1]
        self.two_byte = False
        self.map = None                        # code bytes -> text (certain)
        self.certain = True
        self.widths = {}
        self.default_width = None
        self.core = None
        self.type3 = self.subtype == "/Type3"
        tu = f.get("/ToUnicode")
        tounicode = None
        if tu is not None:
            try:
                tounicode = parse_tounicode(_data(tu))
            except Refuse:
                raise
            except Exception:
                tounicode = None
            if tounicode is None:
                self.certain = False
        if self.subtype == "/Type0":
            enc = _name(f.get("/Encoding"))
            self.two_byte = True
            if enc not in ("/Identity-H",) or tounicode is None:
                self.certain = False           # vertical / predefined CMaps / no ToUnicode
            self.map = tounicode or {}
            desc = _resolve(f.get("/DescendantFonts"))
            d0 = _resolve(desc[0]) if desc else None
            if d0 is not None:
                self.default_width = float(d0.get("/DW", 1000))
                w = _resolve(d0.get("/W"))
                if w is not None:
                    self._cid_widths(w)
            return
        # simple fonts (Type1, TrueType, MMType1, Type3)
        self.map = {}
        enc = _resolve(f.get("/Encoding"))
        table = None
        diffs = {}
        if enc is None:
            if self.base in ("Symbol", "ZapfDingbats"):
                table = None
            elif _resolve(f.get("/FontDescriptor")) is None or self.subtype == "/Type1":
                table = encodings.get("/StandardEncoding")
        elif hasattr(enc, "keys"):
            table = encodings.get(_name(enc.get("/BaseEncoding")) or "/StandardEncoding")
            d = _resolve(enc.get("/Differences"))
            code = 0
            for item in (d or []):
                item = _resolve(item)
                if isinstance(item, (int, float)) or type(item).__name__ in (
                        "NumberObject", "FloatObject"):
                    code = int(item)
                else:
                    diffs[code] = str(item)
                    code += 1
        else:
            table = encodings.get(str(enc))
        for code in range(256):
            ch = None
            if code in diffs:
                ch = _glyph(diffs[code], glyphs)
            elif table is not None:
                ch = table[code]
                if ch is not None and len(ch) == 1 and (
                        __import__("unicodedata").category(ch) == "Cc"):
                    ch = None                  # an undefined slot of the encoding
            if ch:
                self.map[bytes([code])] = ch
        if tounicode is not None:
            for k, v in tounicode.items():
                if len(k) != 1:
                    self.certain = False
                    continue
                if k in self.map and self.map[k] != v and _plain(v) and _plain(self.map[k]):
                    self.certain = False       # two maps that disagree: no certainty
                self.map[k] = v
        if tounicode is None and table is None and not diffs:
            self.certain = False               # symbolic / unknown encoding
        fc = int(_resolve(f.get("/FirstChar", 0)) or 0)
        ws = _resolve(f.get("/Widths"))
        if ws is not None:
            for i, w in enumerate(ws):
                self.widths[fc + i] = float(_resolve(w))
            fd = _resolve(f.get("/FontDescriptor"))
            if fd is not None and fd.get("/MissingWidth") is not None:
                self.default_width = float(_resolve(fd.get("/MissingWidth")))
        else:
            self.core = _core_widths(self.base)

    def _cid_widths(self, w):
        i = 0
        items = [_resolve(x) for x in w]
        while i < len(items) and len(self.widths) < MAX_CMAP_ENTRIES:
            first = int(items[i])
            nxt = items[i + 1] if i + 1 < len(items) else None
            if isinstance(nxt, list) or type(nxt).__name__ == "ArrayObject":
                for k, x in enumerate(nxt):
                    self.widths[first + k] = float(_resolve(x))
                i += 2
            elif i + 2 < len(items):
                last, wd = int(items[i + 1]), float(items[i + 2])
                for c in range(first, min(last, first + 65535) + 1):
                    self.widths[c] = wd
                i += 3
            else:
                break

    def codes(self, s: bytes):
        """[(code int, code bytes, text or None, is_space)]"""
        out = []
        step = 2 if self.two_byte else 1
        if len(s) % step:
            self.certain = False
            s = s[:len(s) - len(s) % step]
        for i in range(0, len(s), step):
            cb = s[i:i + step]
            text = self.map.get(cb)
            out.append((int.from_bytes(cb, "big"), cb, text, step == 1 and cb == b" "))
        return out

    def width(self, code: int, text) -> float:
        if code in self.widths:
            return self.widths[code]
        if self.core is not None and text:
            w = self.core.get(text[:1]) or self.core.get("default")
            if w:
                return float(w)
        if self.default_width is not None:
            return self.default_width
        return 1000.0 if self.two_byte else 500.0


def _plain(s: str) -> bool:
    return bool(s) and all(c.isprintable() for c in s)


def _glyph(name: str, glyphs) -> str:
    n = name if name.startswith("/") else "/" + name
    v = glyphs.get(n)
    if v:
        return v
    bare = n[1:]
    try:
        if bare.startswith("uni") and len(bare) == 7:
            return chr(int(bare[3:], 16))
        if bare.startswith("u") and 5 <= len(bare) <= 7:
            return chr(int(bare[1:], 16))
    except ValueError:
        return None
    return None


# ------------------------------------------------------------------ colours
def _whiteish(space: str, vals) -> bool:
    if space == "G":
        return bool(vals) and vals[0] >= 0.9
    if space == "RGB":
        return len(vals) >= 3 and min(vals[:3]) >= 0.9
    if space == "CMYK":
        return len(vals) >= 4 and max(vals[:4]) <= 0.1
    return False


def _space_of(name: str, resources) -> str:
    if name in ("/DeviceGray", "/G", "/CalGray"):
        return "G"
    if name in ("/DeviceRGB", "/RGB", "/CalRGB"):
        return "RGB"
    if name in ("/DeviceCMYK", "/CMYK"):
        return "CMYK"
    try:
        cs = _resolve(_resolve(resources.get("/ColorSpace")).get(name))
        if isinstance(cs, list) or type(cs).__name__ == "ArrayObject":
            kind = _name(cs[0])
            if kind == "/ICCBased":
                n = int(_resolve(cs[1]).get("/N"))
                return {1: "G", 3: "RGB", 4: "CMYK"}.get(n, "unknown")
            if kind in ("/CalRGB",):
                return "RGB"
            if kind in ("/CalGray",):
                return "G"
        elif cs is not None:
            return _space_of(_name(cs), {})
    except Exception:
        pass
    return "unknown"


# ------------------------------------------------------------------ pages
class _GS:
    __slots__ = ("ctm", "fill", "stroke", "clip", "complex_clip", "fa", "sa", "bad_gs",
                 "font", "size", "tc", "tw", "th", "tl", "tr", "ts")

    def __init__(self):
        self.ctm = ID
        self.fill = ("G", [0.0])
        self.stroke = ("G", [0.0])
        self.clip = None
        self.complex_clip = False
        self.fa = self.sa = 1.0
        self.bad_gs = False
        self.font = None
        self.size = 0.0
        self.tc = self.tw = 0.0
        self.th = 1.0
        self.tl = 0.0
        self.tr = 0
        self.ts = 0.0

    def copy(self):
        g = _GS()
        for k in self.__slots__:
            v = getattr(self, k)
            setattr(g, k, list(v) if isinstance(v, list) else v)
        g.fill = (self.fill[0], list(self.fill[1]))
        g.stroke = (self.stroke[0], list(self.stroke[1]))
        return g


class Page:
    def __init__(self, reader, page, number: int, budget: _Budget):
        self.reader, self.page, self.n, self.budget = reader, page, number, budget
        mb = [float(x) for x in page.mediabox]
        try:
            cb = [float(x) for x in page.cropbox]
        except Exception:
            cb = mb
        self.box = _inter((min(mb[0], mb[2]), min(mb[1], mb[3]), max(mb[0], mb[2]),
                           max(mb[1], mb[3])),
                          (min(cb[0], cb[2]), min(cb[1], cb[3]), max(cb[0], cb[2]),
                           max(cb[1], cb[3])))
        self.flags = set()
        self.runs = []                         # [text, x0, y, x1, size, hidden?]
        self.covers = []
        self.image_area = 0.0
        self.invisible = 0
        self.dropped = 0
        self.fonts = {}
        self.ops = [0]

    def flag(self, f):
        self.flags.add(f)

    # -- run creation
    def _font(self, resources, name):
        key = (id(resources), name)
        if key not in self.fonts:
            try:
                fobj = _resolve(_resolve(resources.get("/Font")).get(name))
                self.fonts[key] = Font(fobj) if fobj is not None else None
            except Refuse:
                raise
            except Exception:
                self.fonts[key] = None
        return self.fonts[key]

    def show(self, gs, tm, s: bytes, resources):
        """Show one string; returns the new text matrix."""
        font = gs.font
        if font is None:
            self.flag("pdf_uncertain_text")
            return tm
        if font.type3:
            self.flag("pdf_type3_font")
        if not font.certain:
            self.flag("pdf_uncertain_text")
        trm0 = _mul(_mul((gs.size * gs.th, 0, 0, gs.size, 0, gs.ts), tm), gs.ctm)
        x0, y0 = trm0[4], trm0[5]
        chars = []
        for code, cb, text, is_space in font.codes(s):
            if text is None:
                self.flag("pdf_uncertain_text")
                text = "�"
            chars.append(text)
            w0 = font.width(code, text) / 1000.0
            tx = (w0 * gs.size + gs.tc + (gs.tw if is_space else 0.0)) * gs.th
            tm = _mul((1, 0, 0, 1, tx, 0), tm)
        text = "".join(chars)
        self.budget.chars += len(text)
        if self.budget.chars > MAX_CHARS_TOTAL:
            raise Refuse("too_complex", "The PDF holds too much text to read safely - nothing "
                                        "was imported.")
        trm1 = _mul(_mul((gs.size * gs.th, 0, 0, gs.size, 0, gs.ts), tm), gs.ctm)
        x1 = trm1[4]
        size = math.hypot(trm0[2], trm0[3])
        if not text.strip():
            return tm
        # -- visibility checks
        a, b, c, d = trm0[:4]
        if size <= 0 or abs(b) > 1e-3 * max(abs(a), 1e-9) or abs(c) > 1e-3 * max(abs(d), 1e-9) \
                or a <= 0 or d <= 0:
            self.flag("pdf_rotated_text")
        hscale = abs(a) / size if size else 0
        if size and not (0.6 <= hscale <= 1.6):
            self.flag("pdf_scaled_text")
        hidden = False
        mode = gs.tr
        if mode in (3, 7):
            hidden = True
        if size < 4.0:
            self.flag("pdf_tiny_text")
        if size < 0.5:
            hidden = True
        fills = mode in (0, 2, 4, 6)
        strokes = mode in (1, 2, 5, 6)
        if not hidden:
            for on, (space, vals), alpha in ((fills, gs.fill, gs.fa), (strokes, gs.stroke, gs.sa)):
                if not on:
                    continue
                if space == "unknown":
                    self.flag("pdf_uncertain_colour")
                elif _whiteish(space, vals):
                    self.flag("pdf_hidden_text")
                if alpha < 0.5:
                    self.flag("pdf_hidden_text")
            if gs.bad_gs:
                self.flag("pdf_uncertain_colour")
        rb = (min(x0, x1), y0 - 0.25 * size, max(x0, x1), y0 + 0.85 * size)
        vis = _inter(rb, self.box)
        if _area(vis) <= 0.0 and _area(rb) > 0:
            hidden = True
            self.dropped += 1
            self.flag("pdf_hidden_text")
        elif _area(vis) < 0.98 * _area(rb):
            self.flag("pdf_hidden_text")
        if gs.clip is not None and not hidden:
            cv = _inter(rb, gs.clip)
            if _area(cv) <= 0.0:
                hidden = True
                self.dropped += 1
                self.flag("pdf_hidden_text")
            elif _area(cv) < 0.98 * _area(rb):
                self.flag("pdf_hidden_text")
        if gs.complex_clip:
            self.flag("pdf_complex_clip")
        if self.oc_depth:
            self.flag("pdf_optional_content")
        if mode in (3, 7):
            self.invisible += 1
        if len(self.runs) >= MAX_RUNS_PAGE:
            raise Refuse("too_complex", "A page holds too many pieces of text to read safely - "
                                        "nothing was imported.")
        self.runs.append([text, round(x0, 2), round(y0, 2), round(x1, 2), round(size, 2),
                          bool(hidden), rb])
        return tm

    def cover(self, box):
        """Something opaque painted over `box`: earlier text under it may be hidden."""
        box = _inter(box, self.box)
        if _area(box) <= 0:
            return
        for r in self.runs:
            rb = r[6]
            if _area(rb) > 0 and _area(_inter(rb, box)) > 0.1 * _area(rb):
                self.flag("pdf_covered_text")
                return

    def image(self, gs):
        b = _box([_pt(gs.ctm, 0, 0), _pt(gs.ctm, 1, 0), _pt(gs.ctm, 0, 1), _pt(gs.ctm, 1, 1)])
        if gs.clip is not None:
            b = _inter(b, gs.clip)
        self.image_area += _area(_inter(b, self.box))
        self.cover(b)

    # -- the interpreter
    def run(self):
        from pypdf.generic import ContentStream
        self.oc_depth = 0
        if int(self.page.get("/Rotate", 0) or 0) % 360:
            self.flag("pdf_rotated_page")
        contents = self.page.get_contents()
        res = _resolve(self.page.get("/Resources")) or {}
        if contents is None:
            return
        data = contents.get_data()
        if len(data) > MAX_STREAM:
            raise Refuse("too_complex", "A page's content is too large once decompressed - "
                                        "nothing was imported.")
        ops = ContentStream(contents, self.reader).operations
        self.interpret(ops, res, _GS(), 0)

    def interpret(self, ops, resources, gs, depth):
        from pypdf.generic import ContentStream
        stack = []
        tm = tlm = ID
        path = []
        simple_rect = True
        pending_clip = None
        mc = []
        for operands, op in ops:
            self.budget.op(self.ops)
            try:
                o = op.decode("latin-1") if isinstance(op, bytes) else str(op)
            except Exception:
                continue
            args = operands
            try:
                if o == "q":
                    if len(stack) > 256:
                        raise Refuse("too_complex", "The PDF nests drawing states too deeply - "
                                                    "nothing was imported.")
                    stack.append(gs.copy())
                elif o == "Q":
                    if stack:
                        gs = stack.pop()
                elif o == "cm":
                    gs.ctm = _mul(tuple(_num(x) for x in args[:6]), gs.ctm)
                elif o == "BT":
                    tm = tlm = ID
                elif o == "ET":
                    pass
                elif o == "Tf":
                    gs.font = self._font(resources, _name(args[0]))
                    gs.size = _num(args[1])
                elif o == "Tc":
                    gs.tc = _num(args[0])
                elif o == "Tw":
                    gs.tw = _num(args[0])
                elif o == "Tz":
                    gs.th = _num(args[0]) / 100.0
                elif o == "TL":
                    gs.tl = _num(args[0])
                elif o == "Tr":
                    gs.tr = int(_num(args[0]))
                elif o == "Ts":
                    gs.ts = _num(args[0])
                elif o == "Td":
                    tlm = _mul((1, 0, 0, 1, _num(args[0]), _num(args[1])), tlm)
                    tm = tlm
                elif o == "TD":
                    gs.tl = -_num(args[1])
                    tlm = _mul((1, 0, 0, 1, _num(args[0]), _num(args[1])), tlm)
                    tm = tlm
                elif o == "Tm":
                    tlm = tm = tuple(_num(x) for x in args[:6])
                elif o == "T*":
                    tlm = _mul((1, 0, 0, 1, 0, -gs.tl), tlm)
                    tm = tlm
                elif o in ("Tj", "'", '"'):
                    if o in ("'", '"'):
                        if o == '"':
                            gs.tw, gs.tc = _num(args[0]), _num(args[1])
                        tlm = _mul((1, 0, 0, 1, 0, -gs.tl), tlm)
                        tm = tlm
                    s = args[-1]
                    tm = self.show(gs, tm, _bytes(s), resources)
                elif o == "TJ":
                    for item in args[0]:
                        if isinstance(item, (bytes, str)) or type(item).__name__ in (
                                "TextStringObject", "ByteStringObject"):
                            tm = self.show(gs, tm, _bytes(item), resources)
                        else:
                            adj = _num(item)
                            tm = _mul((1, 0, 0, 1, -adj / 1000.0 * gs.size * gs.th, 0), tm)
                elif o in ("g", "G"):
                    v = ("G", [_num(args[0])])
                    if o == "g":
                        gs.fill = v
                    else:
                        gs.stroke = v
                elif o in ("rg", "RG"):
                    v = ("RGB", [_num(x) for x in args[:3]])
                    if o == "rg":
                        gs.fill = v
                    else:
                        gs.stroke = v
                elif o in ("k", "K"):
                    v = ("CMYK", [_num(x) for x in args[:4]])
                    if o == "k":
                        gs.fill = v
                    else:
                        gs.stroke = v
                elif o in ("cs", "CS"):
                    sp = _space_of(_name(args[0]), resources)
                    v = (sp, [0.0] if sp == "G" else [0.0, 0.0, 0.0] if sp == "RGB" else
                         [0.0, 0.0, 0.0, 1.0] if sp == "CMYK" else [])
                    if o == "cs":
                        gs.fill = v
                    else:
                        gs.stroke = v
                elif o in ("sc", "scn", "SC", "SCN"):
                    target = gs.fill if o in ("sc", "scn") else gs.stroke
                    nums = []
                    for x in args:
                        try:
                            nums.append(_num(x))
                        except (TypeError, ValueError):
                            target = ("unknown", [])   # a pattern
                    v = (target[0], nums) if target[0] != "unknown" else target
                    if o in ("sc", "scn"):
                        gs.fill = v
                    else:
                        gs.stroke = v
                elif o == "gs":
                    egs = _resolve(_resolve(resources.get("/ExtGState")).get(_name(args[0])))
                    if egs is not None:
                        if egs.get("/ca") is not None:
                            gs.fa = _num(_resolve(egs.get("/ca")))
                        if egs.get("/CA") is not None:
                            gs.sa = _num(_resolve(egs.get("/CA")))
                        sm = egs.get("/SMask")
                        bm = egs.get("/BM")
                        if (sm is not None and _name(sm) != "/None") or egs.get("/Font") is not None \
                                or (bm is not None and _name(bm) not in ("/Normal", "/Compatible")):
                            gs.bad_gs = True
                elif o == "re":
                    x, y, w, h = (_num(v) for v in args[:4])
                    path.append(_box([_pt(gs.ctm, x, y), _pt(gs.ctm, x + w, y),
                                      _pt(gs.ctm, x, y + h), _pt(gs.ctm, x + w, y + h)]))
                    if abs(gs.ctm[1]) > 1e-6 or abs(gs.ctm[2]) > 1e-6:
                        simple_rect = False
                elif o in ("m", "l"):
                    x, y = _num(args[0]), _num(args[1])
                    path.append(_pt(gs.ctm, x, y) * 2)
                    simple_rect = False
                elif o in ("c", "v", "y"):
                    nums = [_num(v) for v in args]
                    pts = [_pt(gs.ctm, nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
                    if pts:
                        path.append(_box(pts))
                    simple_rect = False
                elif o == "h":
                    pass
                elif o in ("W", "W*"):
                    pending_clip = (list(path), simple_rect)
                elif o in ("f", "F", "f*", "B", "B*", "b", "b*", "S", "s", "n"):
                    if o not in ("S", "s", "n") and path:
                        self.cover(_box([(p[0], p[1]) for p in path] +
                                        [(p[2], p[3]) for p in path]))
                    if pending_clip is not None:
                        cp, simple = pending_clip
                        rects = [p for p in cp if len(p) == 4]
                        if simple and len(cp) == 1 and len(rects) == 1:
                            gs.clip = rects[0] if gs.clip is None else _inter(gs.clip, rects[0])
                        elif cp:
                            gs.complex_clip = True
                        pending_clip = None
                    path = []
                    simple_rect = True
                elif o == "Do":
                    xo = _resolve(_resolve(resources.get("/XObject")).get(_name(args[0])))
                    if xo is None:
                        continue
                    sub = _name(xo.get("/Subtype"))
                    if sub == "/Image":
                        self.image(gs)
                    elif sub == "/Form":
                        self.budget.forms += 1
                        if depth >= MAX_FORM_DEPTH or self.budget.forms > MAX_FORMS_TOTAL:
                            raise Refuse("too_complex", "The PDF nests drawings too deeply - "
                                                        "nothing was imported.")
                        g2 = gs.copy()
                        mtx = xo.get("/Matrix")
                        if mtx is not None:
                            g2.ctm = _mul(tuple(_num(_resolve(v)) for v in mtx), g2.ctm)
                        bbox = xo.get("/BBox")
                        if bbox is not None:
                            bb = [_num(_resolve(v)) for v in bbox]
                            rect = _box([_pt(g2.ctm, bb[0], bb[1]), _pt(g2.ctm, bb[2], bb[1]),
                                         _pt(g2.ctm, bb[0], bb[3]), _pt(g2.ctm, bb[2], bb[3])])
                            g2.clip = rect if g2.clip is None else _inter(g2.clip, rect)
                        if len(_data(xo)) > MAX_STREAM:
                            raise Refuse("too_complex", "A drawing is too large - nothing was "
                                                        "imported.")
                        res2 = _resolve(xo.get("/Resources")) or resources
                        self.interpret(ContentStream(xo, self.reader).operations, res2, g2,
                                       depth + 1)
                elif o == "INLINE IMAGE":
                    self.image(gs)
                elif o == "sh":
                    self.cover(gs.clip if gs.clip is not None else self.box)
                elif o in ("BDC", "BMC"):
                    tag = _name(args[0]) if args else ""
                    mc.append(tag == "/OC")
                    if tag == "/OC":
                        self.oc_depth += 1
                elif o == "EMC":
                    if mc and mc.pop():
                        self.oc_depth -= 1
                elif o in ("d0", "d1"):
                    self.flag("pdf_type3_font")
                elif o not in _KNOWN:
                    self.flag("pdf_reader_warning")    # not a PDF operator SK knows
            except Refuse:
                raise
            except (TypeError, ValueError, IndexError, KeyError, AttributeError,
                    OverflowError, ZeroDivisionError):
                self.flag("pdf_reader_warning")    # an operator SK could not follow

    def result(self):
        runs = self.runs
        text_runs = [r for r in runs if not r[5]]
        page_area = _area(self.box)
        images = page_area > 0 and self.image_area >= 0.25 * page_area
        if self.invisible and images:
            self.flag("pdf_ocr_layer")
            kept = [r for r in runs if r[5] and r[6] and _area(_inter(r[6], self.box)) > 0] \
                + text_runs
        else:
            if self.invisible:
                self.flag("pdf_hidden_text")
            kept = text_runs
        kind = "text"
        if not [r for r in kept if r[0].strip()]:
            kind = "image_only" if images else "empty"
        return {"n": self.n, "box": [round(v, 2) for v in self.box], "kind": kind,
                "flags": sorted(self.flags),
                "runs": [r[:5] for r in kept],
                "image_fraction": round(self.image_area / page_area, 3) if page_area else 0}


_KNOWN = frozenset("b B b* B* BDC BI BMC BT BX c cm CS cs d d0 d1 Do DP EI EMC ET EX f F f* "
                   "G g gs h i ID j J K k l m M MP n q Q re RG rg ri s S SC sc SCN scn sh T* "
                   "Tc Td TD Tf Tj TJ TL Tm Tr Ts Tw Tz v w W W* y ' \"".split()) | {"INLINE IMAGE"}


def _bytes(s) -> bytes:
    if isinstance(s, bytes):
        return s
    if hasattr(s, "original_bytes"):
        return s.original_bytes
    return str(s).encode("latin-1", "replace")


# ------------------------------------------------------------------ the document
def read(data: bytes) -> dict:
    import io
    import warnings
    if not data.startswith(b"%PDF-"):
        raise Refuse("not_pdf", "This is not a PDF file (it does not start with %PDF-) - "
                                "nothing was imported.")
    import pypdf
    from pypdf import filters
    for name in ("ZLIB_MAX_OUTPUT_LENGTH", "LZW_MAX_OUTPUT_LENGTH", "MAX_DECLARED_STREAM_LENGTH",
                 "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH", "RUN_LENGTH_MAX_OUTPUT_LENGTH",
                 "JBIG2_MAX_OUTPUT_LENGTH"):
        if hasattr(filters, name):
            setattr(filters, name, MAX_STREAM)
    if hasattr(filters, "JBIG2DEC_BINARY"):
        filters.JBIG2DEC_BINARY = None          # never call an outside program
    problems = []

    class _H(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                problems.append(record.getMessage()[:200])
    logging.getLogger("pypdf").addHandler(_H())
    logging.getLogger("pypdf").setLevel(logging.WARNING)
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        try:
            reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        except Exception as exc:
            raise Refuse("damaged", "The PDF is damaged or not standard (%s) - nothing was "
                                    "imported." % type(exc).__name__)
        try:
            encrypted = reader.is_encrypted
        except Exception:
            encrypted = True
        if encrypted:
            raise Refuse("encrypted", "The PDF is encrypted or restricted by permissions; "
                                      "encrypted PDFs are never imported. Save an unencrypted "
                                      "copy if you may - nothing was imported.")
        try:
            n = len(reader.pages)
        except Exception as exc:
            raise Refuse("damaged", "The PDF's page list is damaged (%s) - nothing was "
                                    "imported." % type(exc).__name__)
        if n == 0:
            raise Refuse("empty", "The PDF has no pages - nothing was imported.")
        if n > MAX_PAGES:
            raise Refuse("too_many_pages", "The PDF has %d pages; at most %d are imported - "
                                           "nothing was imported." % (n, MAX_PAGES))
        budget = _Budget()
        pages = []
        for i in range(n):
            before = len(problems) + len(warned)
            try:
                pg = Page(reader, reader.pages[i], i + 1, budget)
                pg.run()
            except Refuse:
                raise
            except Exception as exc:
                if "Limit" in type(exc).__name__:
                    raise Refuse("too_complex", "Page %d of the PDF is too large once "
                                                "decompressed - nothing was imported." % (i + 1))
                raise Refuse("damaged", "Page %d of the PDF could not be read (%s) - nothing "
                                        "was imported." % (i + 1, type(exc).__name__))
            if len(problems) + len(warned) > before:
                pg.flag("pdf_reader_warning")
            pages.append(pg.result())
        info = []
        if data.count(b"%%EOF") > 1 or data.count(b"startxref") > 1:
            info.append("pdf_incremental_updates")
        try:
            root = reader.trailer["/Root"]
            if "/Names" in root and "/JavaScript" in _resolve(root["/Names"]):
                info.append("pdf_javascript")
            if "/OpenAction" in root or "/AA" in root:
                info.append("pdf_actions")
            if "/AcroForm" in root:
                info.append("pdf_form_fields_ignored")
        except Exception:
            pass
        if any("/Annots" in reader.pages[i] for i in range(n)):
            info.append("pdf_annotations_ignored")
    kinds = [p["kind"] for p in pages]
    if all(k == "image_only" for k in kinds):
        raise Refuse("scanned", "The PDF holds only images of pages (a scan); reading text from "
                                "images (OCR) is not supported yet - nothing was imported.")
    if all(k in ("image_only", "empty") for k in kinds):
        raise Refuse("no_text", "The PDF holds no text SK can read - nothing was imported.")
    return {"pages": pages, "info": sorted(set(info))}


def _limit_memory():
    try:
        import resource
        lim = 1536 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
        resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    except Exception:                          # Windows: no such limits in the stdlib
        pass


def main() -> int:
    _limit_memory()
    data = sys.stdin.buffer.read()
    try:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            raise Refuse("no_reader", "PDF import needs the pypdf package (see "
                                      "requirements-brain.txt) - nothing was imported.")
        out = read(data)
    except Refuse as r:
        out = {"refused": r.code, "message": r.message}
    except MemoryError:
        out = {"refused": "too_complex", "message": "The PDF needs too much memory to read "
                                                    "safely - nothing was imported."}
    except RecursionError:
        out = {"refused": "too_complex", "message": "The PDF nests its parts too deeply - "
                                                    "nothing was imported."}
    except Exception as exc:
        out = {"refused": "damaged", "message": "The PDF could not be read (%s) - nothing was "
                                                "imported." % type(exc).__name__}
    sys.stdout.write(json.dumps(out, ensure_ascii=True))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
