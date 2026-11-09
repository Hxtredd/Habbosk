"""B6 import: plain text, Markdown and saved HTML -> canonical text plus blocks.

    parsed = import_file(path)            # read_file + parse(fmt_for(path))
    parsed = parse(data_bytes, "html")

Everything later in SK Brain (passages, quotes, evidence checks) refers to
POSITIONS in ``Parsed.text`` - the canonical text. A position is only worth
something if it is exact and the same every time, so:

  * the file is read as bytes, refused if it is not a regular file, a symlink,
    too big, or not valid UTF-8 / UTF-16-with-BOM (no guessing of encodings);
  * ``canonical()`` does exactly NFC + line-ending normalisation, nothing else;
  * every ``Block`` satisfies ``text[start:end].strip() == text[start:end]``;
  * the parsers are linear-time single passes with fixed limits.

HTML is never rendered, executed or fetched. Only text a reader would see is
kept; head, scripts, styles, templates, form controls, comments, hidden
elements and every attribute value are dropped. Accuracy and safety beat
coverage: when unsure, text is left out (or flagged ``html_not_rendered``).
Standard library only.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from array import array
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from .store import BrainError

MAX_BYTES = 5 * 1024 * 1024
PDF_MAX_BYTES = 25 * 1024 * 1024   # owner decision: PDFs up to 25 MB (and 300 pages)
MAX_DEPTH = 256            # HTML element nesting
MAX_ELEMENTS = 200_000     # HTML elements
FORMATS = ("text", "markdown", "html", "pdf")

_EXTENSIONS = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
}


class ImportRefused(BrainError):
    """A file that cannot be imported safely; nothing was stored."""


@dataclass(frozen=True)
class Block:
    """One structural piece of the canonical text (end exclusive)."""
    kind: str          # paragraph | heading | list_item | table_row | code | quote | raw_html
    start: int
    end: int
    heading_path: str  # "Title > Subtitle"; a heading's own path includes itself
    level: int = 0     # heading level 1-6, else 0
    # PDF only (B6): the page (1-based), the block's box on it [x0, y0, x1, y1] in PDF
    # points, and page warnings every passage of the block carries
    page: int = 0
    bbox: tuple = ()
    flags: tuple = ()


@dataclass(frozen=True)
class Parsed:
    """The result of an import. Every position refers to ``text``."""
    text: str
    fmt: str
    blocks: tuple
    bytes_sha: str
    text_sha: str
    flags: tuple


# ----------------------------------------------------------------- reading

def read_file(path: str, limit: int = MAX_BYTES) -> bytes:
    """Read a regular file (not a symlink) of at most `limit` bytes, as bytes."""
    if not isinstance(path, str) or not path:
        raise ImportRefused("No file path was given - nothing was imported.")
    if os.path.islink(path):
        raise ImportRefused("The path is a symbolic link; links are not followed - "
                            "nothing was imported.")
    if not os.path.isfile(path):
        raise ImportRefused("The path is not a regular file - nothing was imported.")
    # O_BINARY: without it Windows opens the file in TEXT mode - every CR LF is read
    # as LF and reading stops at the first 0x1A byte (PDFs then look damaged)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | \
        getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ImportRefused("The file could not be opened (%s) - nothing was imported."
                            % exc.strerror) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ImportRefused("The path is not a regular file - nothing was imported.")
        if st.st_size > limit:
            raise ImportRefused("The file is larger than %d bytes - nothing was imported."
                                % limit)
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ImportRefused("The file is larger than %d bytes - nothing was "
                                    "imported." % limit)
            chunks.append(chunk)
    except OSError as exc:
        raise ImportRefused("The file could not be read (%s) - nothing was imported."
                            % exc.strerror) from None
    finally:
        os.close(fd)
    return b"".join(chunks)


_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE = b"\xff\xfe"
_UTF16_BE = b"\xfe\xff"


def _bom_kind(data: bytes) -> str:
    if data.startswith(_UTF8_BOM):
        return "utf8"
    if data.startswith(_UTF16_LE) or data.startswith(_UTF16_BE):
        return "utf16"
    return ""


def decode(data: bytes) -> str:
    """UTF-8 (BOM stripped), UTF-16 with BOM, or strict UTF-8. Nothing else."""
    try:
        if data.startswith(_UTF8_BOM):
            text = data[3:].decode("utf-8")
        elif data.startswith(_UTF16_LE):
            text = data[2:].decode("utf-16-le")
        elif data.startswith(_UTF16_BE):
            text = data[2:].decode("utf-16-be")
        else:
            text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ImportRefused("The file is not UTF-8 / UTF-16 with BOM - nothing was "
                            "imported.") from None
    if "\x00" in text:
        raise ImportRefused("The file contains NUL characters - nothing was imported.")
    return text


def canonical(text: str) -> str:
    """NFC, then CRLF -> LF, then a lone CR -> LF. Nothing else."""
    text = unicodedata.normalize("NFC", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fmt_for(path: str) -> str:
    """The format for a file name, by extension; anything else is refused."""
    ext = os.path.splitext(str(path))[1].lower()
    fmt = _EXTENSIONS.get(ext)
    if fmt is None:
        raise ImportRefused("Unknown file type %r (use .txt, .md, .markdown, .html, "
                            ".htm, .pdf) - nothing was imported." % ext)
    return fmt


# ------------------------------------------------------------ shared helpers

def _trim(text: str, s: int, e: int) -> Optional[Tuple[int, int]]:
    """Shrink [s, e) so it has no leading / trailing whitespace; None if empty."""
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    if s >= e:
        return None
    return s, e


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_blocks(text: str, blocks: List[Block]) -> None:
    """Internal guard: blocks are ordered, in bounds and exactly trimmed."""
    last = 0
    for b in blocks:
        if not (last <= b.start < b.end <= len(text)):
            raise ImportRefused("Internal position error - nothing was imported.")
        seg = text[b.start:b.end]
        if seg != seg.strip():
            raise ImportRefused("Internal position error - nothing was imported.")
        last = b.end


# --------------------------------------------------------------- plain text

def _parse_text(text: str) -> List[Block]:
    """Paragraphs separated by blank lines."""
    blocks: List[Block] = []
    pos = 0
    n = len(text)
    para_s = -1
    para_e = -1
    while pos <= n:
        nl = text.find("\n", pos)
        end = n if nl < 0 else nl
        if text[pos:end].strip():
            if para_s < 0:
                para_s = pos
            para_e = end
        elif para_s >= 0:
            t = _trim(text, para_s, para_e)
            if t:
                blocks.append(Block("paragraph", t[0], t[1], ""))
            para_s = -1
        if nl < 0:
            break
        pos = nl + 1
    if para_s >= 0:
        t = _trim(text, para_s, para_e)
        if t:
            blocks.append(Block("paragraph", t[0], t[1], ""))
    return blocks


# ----------------------------------------------------------------- Markdown

def _indent(line: str) -> int:
    """Leading indentation in columns (a tab counts 4)."""
    cols = 0
    for ch in line:
        if ch == " ":
            cols += 1
        elif ch == "\t":
            cols += 4
        else:
            break
    return cols


def _first_nonblank(line: str) -> int:
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    return i


def _fence_open(line: str):
    """(char, length) when the line opens a code fence, else None."""
    j = 0
    while j < len(line) and line[j] == " ":
        j += 1
    if j > 3 or j >= len(line) or line[j] not in "`~":
        return None
    ch = line[j]
    k = j
    while k < len(line) and line[k] == ch:
        k += 1
    if k - j < 3:
        return None
    if ch == "`" and "`" in line[k:]:
        return None
    return ch, k - j


def _fence_close(line: str, ch: str, n: int) -> bool:
    s = line.strip()
    return len(s) >= n and s == ch * len(s) and _indent(line) < 4


def _is_thematic(line: str) -> bool:
    s = line.strip()
    if not s or s[0] not in "-*_" or _indent(line) >= 4:
        return False
    core = s.replace(" ", "").replace("\t", "")
    return len(core) >= 3 and core == core[0] * len(core)


def _atx(line: str):
    """(level, start_idx, end_idx) of the heading text inside the line, or None."""
    if _indent(line) >= 4:
        return None
    j = _first_nonblank(line)
    k = j
    while k < len(line) and line[k] == "#":
        k += 1
    level = k - j
    if level < 1 or level > 6:
        return None
    if k < len(line) and line[k] not in " \t":
        return None
    if k >= len(line):
        return None
    end = len(line)
    body = line[k:end].rstrip()
    if body.strip() and body.endswith("#"):
        stripped = body.rstrip("#")
        if not stripped.strip():
            end = k          # only closing hashes: empty heading
        elif stripped[-1] in " \t":
            end = k + len(stripped)
    elif not body.strip():
        end = k
    return level, k, end


def _list_marker(line: str):
    """(content_index, number_or_None) when the line starts a list item, else None."""
    j = _first_nonblank(line)
    if j >= len(line):
        return None
    ch = line[j]
    if ch in "-*+":
        if j + 1 == len(line) or line[j + 1] in " \t":
            return j + 1, None
        return None
    k = j
    while k < len(line) and line[k].isdigit() and line[k].isascii() and k - j < 9:
        k += 1
    if k == j or k >= len(line) or line[k] not in ".)":
        return None
    if k + 1 == len(line) or line[k + 1] in " \t":
        return k + 1, int(line[j:k])
    return None


def _is_sep(line: str) -> bool:
    """A table separator line like |---|:---:|."""
    s = line.strip()
    if "|" not in s or "-" not in s or _indent(line) >= 4:
        return False
    for ch in s:
        if ch not in "|-: \t":
            return False
    cells = s.strip("|").split("|")
    for c in cells:
        c = c.strip()
        core = c.strip(":")
        if not core or core != "-" * len(core) or len(c) - len(core) > 2:
            return False
    return True


def _parse_markdown(text: str) -> List[Block]:
    """Line-based Markdown block parser (linear time, markup excluded from blocks)."""
    lines = text.split("\n")
    n = len(lines)
    starts = array("q", [0]) * n
    pos = 0
    for i in range(n):
        starts[i] = pos
        pos += len(lines[i]) + 1

    blocks: List[Block] = []
    hstack: List[Tuple[int, str]] = []
    path = [""]

    def add(kind: str, s: int, e: int, level: int = 0) -> None:
        t = _trim(text, s, e)
        if t is None:
            return
        if kind == "heading":
            while hstack and hstack[-1][0] >= level:
                hstack.pop()
            hstack.append((level, text[t[0]:t[1]]))
            path[0] = " > ".join(h[1] for h in hstack)
        blocks.append(Block(kind, t[0], t[1], path[0], level))

    def line_end(i: int) -> int:
        return starts[i] + len(lines[i])

    def blank(i: int) -> bool:
        return not lines[i].strip()

    def table_start(i: int) -> bool:
        return "|" in lines[i] and i + 1 < n and _is_sep(lines[i + 1])

    def interrupts(j: int) -> bool:
        """Does line j end a running paragraph?"""
        lj = lines[j]
        if _indent(lj) >= 4:
            return False
        if _fence_open(lj) or _is_thematic(lj) or _atx(lj):
            return True
        if lj.lstrip(" ").startswith(">") or lj.lstrip(" ").startswith("<"):
            return True
        m = _list_marker(lj)
        if m and (m[1] is None or m[1] == 1):
            return True
        return table_start(j)

    in_list = False
    i = 0
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        ind = _indent(line)
        s0 = starts[i]

        fence = _fence_open(line) if ind < 4 else None
        if fence:
            j = i + 1
            while j < n and not _fence_close(lines[j], fence[0], fence[1]):
                j += 1
            if j > i + 1:
                add("code", starts[i + 1], line_end(j - 1))
            i = j + 1
            in_list = False
            continue

        if ind < 4:
            if _is_thematic(line):
                i += 1
                in_list = False
                continue
            atx = _atx(line)
            if atx:
                add("heading", s0 + atx[1], s0 + atx[2], atx[0])
                i += 1
                in_list = False
                continue
            stripped = line.lstrip(" ")
            if stripped.startswith(">"):
                j = i
                while j < n and lines[j].lstrip(" ").startswith(">"):
                    j += 1
                gt = s0 + (len(line) - len(stripped)) + 1
                if gt < len(text) and text[gt] == " ":
                    gt += 1
                add("quote", gt, line_end(j - 1))
                i = j
                in_list = False
                continue
            if stripped.startswith("<"):
                j = i
                while j < n and not blank(j):
                    j += 1
                add("raw_html", s0, line_end(j - 1))
                i = j
                in_list = False
                continue

        marker = _list_marker(line) if (ind < 4 or in_list) else None
        if marker:
            j = i + 1
            while j < n and not blank(j) and _indent(lines[j]) >= 2 \
                    and not _list_marker(lines[j]):
                j += 1
            add("list_item", s0 + marker[0], line_end(j - 1))
            i = j
            in_list = True
            continue

        if ind >= 4 and not in_list:
            j = i
            last = i
            while j < n:
                if blank(j):
                    j += 1
                    continue
                if _indent(lines[j]) >= 4:
                    last = j
                    j += 1
                    continue
                break
            add("code", s0, line_end(last))
            i = last + 1
            continue

        if table_start(i):
            add("table_row", *_row_range(text, s0, line_end(i)))
            j = i + 2
            while j < n and not blank(j) and "|" in lines[j]:
                add("table_row", *_row_range(text, starts[j], line_end(j)))
                j += 1
            i = j
            in_list = False
            continue

        j = i + 1
        while j < n and not blank(j) and not interrupts(j):
            j += 1
        add("paragraph", s0, line_end(j - 1))
        i = j
        if ind < 2:
            in_list = False
    return blocks


def _row_range(text: str, s: int, e: int) -> Tuple[int, int]:
    """Cut the leading / trailing '|' (and spaces) off a table row line."""
    t = _trim(text, s, e)
    if t is None:
        return s, s
    s, e = t
    if text[s] == "|":
        s += 1
    t = _trim(text, s, e)
    if t is None:
        return s, s
    s, e = t
    if text[e - 1] == "|":
        e -= 1
    return s, e


# --------------------------------------------------------------------- HTML

_DROP_TAGS = frozenset((
    "head", "title", "script", "style", "template", "noscript", "iframe", "object",
    "embed", "svg", "math", "canvas", "audio", "video", "input", "textarea", "select",
    "option", "optgroup", "button", "datalist", "frame", "frameset", "noframes",
    "applet", "noembed", "meta", "link", "base",
))
_VOID_TAGS = frozenset((
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr", "frame", "keygen",
))
_HEAD_OK = frozenset(("head", "title", "meta", "link", "base", "script", "style",
                      "noscript", "template", "html"))
_HEADINGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))
_BLOCK_TAGS = frozenset((
    "p", "div", "section", "article", "main", "header", "footer", "nav", "aside",
    "figcaption", "dd", "dt", "address", "caption", "ul", "ol", "dl", "menu", "thead",
    "tbody", "tfoot", "figure", "details", "summary", "fieldset", "legend", "form",
    "center", "hgroup", "body", "html", "search", "dir",
))
_CLOSES_P = _BLOCK_TAGS | _HEADINGS | frozenset(("pre", "blockquote", "table", "li", "tr"))
_INLINE_TAGS = frozenset((
    "a", "b", "i", "u", "em", "strong", "span", "font", "small", "big", "code", "sub",
    "sup", "s", "strike", "tt", "mark", "abbr", "cite", "q", "kbd", "samp", "var",
    "label", "bdi", "bdo", "data", "time", "del", "ins", "dfn",
))
_WS = re.compile(r"[ \t\n\r\f]+")
_ZERO_PREFIX = frozenset("0123456789.")


def _num_prefix(value: str):
    """(number, rest) from the start of a CSS value, or None."""
    i = 0
    if value[:1] in "+-":
        i = 1
    j = i
    while j < len(value) and value[j] in _ZERO_PREFIX:
        j += 1
    digits = value[i:j]
    if not any(c.isdigit() for c in digits):
        return None
    try:
        num = float(digits)
    except ValueError:
        return None
    if value[:1] == "-":
        num = -num
    return num, value[j:]


def _is_zero(value: str, allow_unit: bool = True) -> bool:
    got = _num_prefix(value)
    if got is None or got[0] != 0:
        return False
    rest = got[1]
    return rest == "" or (allow_unit and (rest.isalpha() or rest == "%"))


def _strip_css_comments(value: str) -> str:
    out = []
    i = 0
    while True:
        j = value.find("/*", i)
        if j < 0:
            out.append(value[i:])
            break
        out.append(value[i:j])
        k = value.find("*/", j + 2)
        if k < 0:
            break
        i = k + 2
    return "".join(out)


def _style_hidden(value: str) -> bool:
    """Does an inline style hide the element? Unsure counts as hidden."""
    if "\\" in value:
        return True
    css = "".join(_strip_css_comments(value).lower().split())
    decls = {}
    for part in css.split(";"):
        prop, sep, val = part.partition(":")
        if not sep:
            continue
        val = val.replace("!important", "")
        decls[prop] = val
        if prop == "display" and val == "none":
            return True
        if prop == "visibility" and val in ("hidden", "collapse"):
            return True
        if prop == "content-visibility" and val == "hidden":
            return True
        if prop in ("font-size", "opacity") and _is_zero(val):
            return True
        if prop == "font" and re.match(r"^[0.]+[a-z%]*(/|$)", val):
            return True
        if prop == "color" and (val == "transparent" or
                                re.match(r"^(rgba|hsla)\(.*,(0|0\.0*|\.0+|0%)\)$", val)):
            return True
        if prop in ("text-indent", "left", "right", "top", "bottom", "margin-left",
                    "margin-right", "margin-top"):
            got = _num_prefix(val)
            if got is not None and got[0] <= -500:
                return True
        if prop == "clip" and val.replace("px", "") == "rect(0,0,0,0)":
            return True
        if prop == "transform" and ("scale(0)" in val or "scale(0,0)" in val):
            return True
    if decls.get("overflow") in ("hidden", "clip"):
        for prop in ("width", "height", "max-width", "max-height"):
            if prop in decls and _is_zero(decls[prop]):
                return True
    return False


def _own_hidden(tag: str, attrs) -> bool:
    if tag in _DROP_TAGS:
        return True
    for name, value in attrs:
        v = (value or "").strip().lower()
        if name == "hidden":
            return True
        if name == "aria-hidden" and v == "true":
            return True
        if name == "type" and v == "hidden":
            return True
        if name == "style" and value and _style_hidden(value):
            return True
    return False


class _Entry:
    __slots__ = ("tag", "drop", "root", "role")

    def __init__(self, tag: str, drop: bool, root: bool, role: Optional[str]):
        self.tag = tag
        self.drop = drop
        self.root = root
        self.role = role


class _HtmlText(HTMLParser):
    """Collects the visible text of an HTML file as blocks. Never fetches anything.

    A stack of open elements is kept. Everything inside a dropped (hidden or
    non-visible) element is dropped. An end tag may not close an element that
    lies beyond a dropped element's own start tag, so hidden regions end only
    at their own end tag (or at the end of the file).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[_Entry] = []
        self.tag_pos = {}          # tag -> indices in stack
        self.roots: List[int] = []  # stack indices of elements dropped in their own right
        self.n_elems = 0
        self.cur = None            # [kind, level, parts, raw]
        self.cell = None
        self.cell_idx = -1
        self.row = None
        self.row_idx = -1
        self.nested = 0
        self.pre = 0
        self.kinds: List[str] = []
        self.out: List[str] = []
        self.size = 0
        self.blocks: List[Block] = []
        self.hstack: List[Tuple[int, str]] = []
        self.path = ""

    # -- output ----------------------------------------------------------
    @staticmethod
    def _finish(parts, raw: bool) -> str:
        s = canonical("".join(parts))
        if raw:
            return s.strip()
        return _WS.sub(" ", s).strip()

    def _emit(self, kind: str, text: str, level: int = 0) -> None:
        if self.out:
            self.out.append("\n\n")
            self.size += 2
        start = self.size
        self.out.append(text)
        self.size += len(text)
        self.blocks.append(Block(kind, start, self.size, self.path, level))

    def _flush(self) -> None:
        cur = self.cur
        if cur is None:
            return
        self.cur = None
        kind, level, parts, raw = cur
        text = self._finish(parts, raw)
        if not text:
            return
        if kind == "heading":
            while self.hstack and self.hstack[-1][0] >= level:
                self.hstack.pop()
            self.hstack.append((level, text))
            self.path = " > ".join(h[1] for h in self.hstack)
        self._emit(kind, text, level)

    def _boundary(self) -> None:
        if self.cell is not None:
            self.cell.append(" ")
        else:
            self._flush()

    def _begin(self, kind: str, level: int = 0, raw: bool = False) -> None:
        self._flush()
        self.cur = [kind, level, [], raw]

    def _text(self, data: str) -> None:
        if self.stack and self.stack[-1].drop:
            return
        if self.cell is not None:
            self.cell.append(data)
            return
        if self.cur is None:
            if not data.strip():
                return
            self.cur = [self.kinds[-1] if self.kinds else "paragraph", 0, [], False]
        self.cur[2].append(data)

    # -- stack -----------------------------------------------------------
    def _last(self, tag: str) -> int:
        pos = self.tag_pos.get(tag)
        return pos[-1] if pos else -1

    def _pop(self) -> None:
        e = self.stack.pop()
        self.tag_pos[e.tag].pop()
        if e.root:
            self.roots.pop()
        r = e.role
        if r == "heading":
            self._flush()
        elif r == "pre":
            self.pre -= 1
            self._flush()
        elif r == "li":
            self._flush()
            self.kinds.pop()
        elif r == "bq":
            self._boundary()
            self.kinds.pop()
        elif r == "block":
            self._boundary()
        elif r == "ntable":
            self.nested -= 1
            self._boundary()
        elif r == "cell":
            text = self._finish(self.cell, False)
            self.cell = None
            if text and self.row is not None:
                self.row.append(text)
        elif r == "row":
            self._flush()
            row = self.row
            self.row = None
            if row:
                self._emit("table_row", " | ".join(row))

    def _pop_to(self, size: int) -> None:
        while len(self.stack) > size:
            self._pop()

    def _implicit_close(self, tag: str) -> None:
        """The few implied end tags that real pages rely on."""
        st = self.stack
        if tag in _CLOSES_P:
            i = len(st) - 1
            while i >= 0 and st[i].tag in _INLINE_TAGS:
                i -= 1
            if i >= 0 and st[i].tag == "p":
                self._pop_to(i)
        if tag == "li":
            m = self._last("li")
            if m > max(self._last("ul"), self._last("ol"), self._last("menu")):
                self._pop_to(m)
        elif tag in ("dt", "dd"):
            m = max(self._last("dt"), self._last("dd"))
            if m > self._last("dl"):
                self._pop_to(m)

    def _structure(self, tag: str) -> Optional[str]:
        """Start a block for a visible element; returns the element's role."""
        if self.pre:
            return None
        in_cell = self.cell is not None
        if tag in _HEADINGS:
            if in_cell:
                self._boundary()
                return None
            self._begin("heading", int(tag[1]))
            return "heading"
        if tag == "pre":
            if in_cell:
                self._boundary()
                return None
            self._begin("code", 0, True)
            self.pre += 1
            return "pre"
        if tag == "li":
            if in_cell:
                self._boundary()
                return None
            self._begin("list_item")
            self.kinds.append("list_item")
            return "li"
        if tag == "blockquote":
            self._boundary()
            self.kinds.append("quote")
            return "bq"
        if tag == "table":
            self._boundary()
            if in_cell:
                self.nested += 1
                return "ntable"
            return "block"
        if tag == "tr":
            if in_cell and self.nested:
                self._boundary()
                return None
            if self.row is not None:
                self._pop_to(self.row_idx)
            self._flush()
            self.row = []
            self.row_idx = len(self.stack)
            return "row"
        if tag in ("td", "th"):
            if in_cell and self.nested:
                self._boundary()
                return None
            if in_cell:
                self._pop_to(self.cell_idx)
            if self.row is None:
                return None
            self.cell = []
            self.cell_idx = len(self.stack)
            return "cell"
        if tag in _BLOCK_TAGS:
            self._boundary()
            return "block"
        return None

    # -- parser callbacks ------------------------------------------------
    def handle_starttag(self, tag, attrs):
        self.n_elems += 1
        if self.n_elems > MAX_ELEMENTS:
            raise ImportRefused("The HTML has more than %d elements - nothing was "
                                "imported." % MAX_ELEMENTS)
        st = self.stack
        if st and st[-1].tag == "head" and tag not in _HEAD_OK:
            self._pop_to(len(st) - 1)
        if tag in ("option", "optgroup"):       # their end tags are optional
            m = self._last("option")
            if m > self._last("select"):
                self._pop_to(m)
        parent_drop = bool(st) and st[-1].drop
        own = _own_hidden(tag, attrs)
        drop = parent_drop or own
        if not drop:
            self._implicit_close(tag)
        if tag in _VOID_TAGS:
            if not drop:
                if tag == "br":
                    self._text("\n" if self.pre else " ")
                elif tag == "hr":
                    self._boundary()
            return
        role = None if drop else self._structure(tag)
        if own:
            self.roots.append(len(st))
        st.append(_Entry(tag, drop, own, role))
        self.tag_pos.setdefault(tag, []).append(len(st) - 1)
        if len(st) > MAX_DEPTH:
            raise ImportRefused("The HTML is nested deeper than %d levels - nothing was "
                                "imported." % MAX_DEPTH)

    def handle_startendtag(self, tag, attrs):
        # "<div/>" is NOT self-closing in HTML; treat it as an open tag so a
        # hidden element can never end early.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        pos = self.tag_pos.get(tag)
        if not pos:
            return
        m = pos[-1]
        if tag in ("select", "datalist", "optgroup"):   # open options end with them
            while len(self.stack) - 1 > m and self.stack[-1].tag in ("option", "optgroup"):
                self._pop()
        if self.roots and m < self.roots[-1]:
            return
        self._pop_to(m)

    def handle_data(self, data):
        self._text(data)

    # comments, CDATA, declarations and processing instructions are dropped
    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass

    def finish(self):
        """Parse is over: close everything, return (text, blocks)."""
        # An unfinished tag / comment at the end of the file is dropped, never
        # shown as text.
        if self.rawdata.startswith("<"):
            self.rawdata = ""
        self.close()
        self._pop_to(0)
        self._flush()
        return "".join(self.out), self.blocks


def _parse_html(text: str) -> Tuple[str, List[Block]]:
    parser = _HtmlText()
    try:
        parser.feed(text)
        return parser.finish()
    except ImportRefused:
        raise
    except Exception:  # any parser trouble: never a partial import
        raise ImportRefused("The HTML could not be read safely - nothing was "
                            "imported.") from None


# --------------------------------------------------------------------- API

def parse(data: bytes, fmt: str) -> Parsed:
    """Bytes of a file + format -> Parsed (canonical text, blocks, hashes, flags)."""
    if fmt not in FORMATS:
        raise ImportRefused("Unknown format %r - nothing was imported." % (fmt,))
    if not isinstance(data, (bytes, bytearray)):
        raise ImportRefused("Expected the file's bytes - nothing was imported.")
    data = bytes(data)
    limit = PDF_MAX_BYTES if fmt == "pdf" else MAX_BYTES
    if len(data) > limit:
        raise ImportRefused("The file is larger than %d bytes - nothing was imported."
                            % limit)
    if fmt == "pdf":
        from .pdf_import import parse_pdf
        return parse_pdf(data)
    flags: List[str] = []
    bom = _bom_kind(data)
    if bom:
        flags.append("bom")
    if bom == "utf16":
        flags.append("utf16")
    text = canonical(decode(data))
    if fmt == "text":
        blocks = _parse_text(text)
    elif fmt == "markdown":
        blocks = _parse_markdown(text)
    else:
        flags.append("html_not_rendered")
        built, blocks = _parse_html(text)
        text = canonical(built)
        if text != built:
            raise ImportRefused("Internal position error - nothing was imported.")
    _check_blocks(text, blocks)
    try:
        text_sha = _sha(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ImportRefused("The text holds invalid characters - nothing was "
                            "imported.") from None
    return Parsed(text=text, fmt=fmt, blocks=tuple(blocks), bytes_sha=_sha(data),
                  text_sha=text_sha, flags=tuple(flags))


def import_file(path: str, fmt: Optional[str] = None) -> Parsed:
    """read_file + parse; the format comes from the extension unless given."""
    chosen = fmt or fmt_for(path)
    return parse(read_file(path, PDF_MAX_BYTES if chosen == "pdf" else MAX_BYTES), chosen)
