"""B6 PDF import: the PDF is read in a SEPARATE, time-limited process
(brain/pdf_reader.py, pypdf), and only its checked result - pages of
positioned text pieces - is laid out here into the canonical text.

    parsed = parse_pdf(pdf_bytes)      # importer.parse(data, "pdf") calls this

Layout, conservatively and deterministically:
  * every block (paragraph, heading, list item, table row) lies on ONE page and
    carries that page's number, its box and the page's warnings; a passage
    therefore never crosses a page;
  * lines are rebuilt from text positions; pieces far apart on one line are
    separate segments; aligned segments over several lines are table rows
    (never evidence); clearly separated columns are read column by column; a
    line that does not fit the columns marks the page ``pdf_layout_uncertain``;
  * repeated lines in the top / bottom margin (running headers, footers, page
    numbers) are kept as page furniture: in the text, but no passage;
  * a word broken at a line end ("main-" / "tains") stays broken ("-" + new
    line): a quotation must match it exactly or it is refused.
Page warnings (any of pdf_reader.BLOCKING, plus pdf_layout_uncertain /
pdf_overlapping_text) go to every block of that page: the B6 gates then never
let its passages support, challenge or become suggestions. Image-only pages
are listed (``pdf_image_only_pages``); OCR is not part of B6.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from typing import List, Optional

from .importer import Block, ImportRefused, Parsed, canonical

TIMEOUT = 90                       # seconds for the reading process
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAGE_FLAGS = ("pdf_layout_uncertain", "pdf_overlapping_text")
_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); "
         "from puzzle_assistant.brain import pdf_reader; sys.exit(pdf_reader.main())")


# ------------------------------------------------------------------ the reading process
def read_in_child(data: bytes, timeout: float = TIMEOUT) -> dict:
    """Run pdf_reader in its own isolated Python process; never trust it beyond
    the shape of its answer."""
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "PATH", "TEMP",
                                                                   "TMP", "HOME", "LANG")}
    try:
        r = subprocess.run([sys.executable, "-I", "-c", _BOOT, ROOT], input=data,
                           capture_output=True, timeout=timeout, env=env, **kw)
    except subprocess.TimeoutExpired:
        raise ImportRefused("Reading the PDF took too long (more than %d s) - nothing was "
                            "imported." % timeout) from None
    except OSError as exc:
        raise ImportRefused("The PDF reader could not be started (%s) - nothing was "
                            "imported." % exc.strerror) from None
    if r.returncode != 0 or not r.stdout:
        raise ImportRefused("The PDF reader stopped unexpectedly (it may be too large or "
                            "damaged) - nothing was imported.")
    try:
        out = json.loads(r.stdout.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise ImportRefused("The PDF reader gave an unreadable answer - nothing was "
                            "imported.") from None
    if not isinstance(out, dict):
        raise ImportRefused("The PDF reader gave an unreadable answer - nothing was imported.")
    if "refused" in out:
        raise ImportRefused(str(out.get("message") or "The PDF was refused")[:400])
    return _validated(out)


def _validated(out: dict) -> dict:
    bad = ImportRefused("The PDF reader gave an unreadable answer - nothing was imported.")
    pages = out.get("pages")
    if not isinstance(pages, list) or not pages or len(pages) > 300:
        raise bad
    for i, p in enumerate(pages, 1):
        if not isinstance(p, dict) or p.get("n") != i or p.get("kind") not in (
                "text", "image_only", "empty"):
            raise bad
        box = p.get("box")
        if not (isinstance(box, list) and len(box) == 4 and all(_finite(v) for v in box)):
            raise bad
        if not isinstance(p.get("flags"), list) or not all(isinstance(f, str) and
                                                           f.startswith("pdf_")
                                                           for f in p["flags"]):
            raise bad
        runs = p.get("runs")
        if not isinstance(runs, list):
            raise bad
        for r in runs:
            if not (isinstance(r, list) and len(r) == 5 and isinstance(r[0], str)
                    and all(_finite(v) for v in r[1:])):
                raise bad
    if not isinstance(out.get("info", []), list):
        raise bad
    return out


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ------------------------------------------------------------------ layout
_BULLET = re.compile(r"^\s*(?:[•◦▪‣–—*\-]|\d{1,3}[.)])\s+")
_PAGE_NO = re.compile(r"^\W*(?:page|p\.|página|pág\.)?\s*\d{1,4}(?:\s*(?:of|/|de)\s*\d{1,4})?\W*$",
                      re.IGNORECASE)


class _Seg:
    __slots__ = ("text", "x0", "x1", "y", "size")

    def __init__(self, text, x0, x1, y, size):
        self.text, self.x0, self.x1, self.y, self.size = text, x0, x1, y, size


def _lines(runs, flags):
    """Runs -> lines (by baseline) -> segments (split at big gaps)."""
    runs = sorted(runs, key=lambda r: (-r[2], r[1]))
    lines: List[list] = []
    for r in runs:
        text, x0, y, x1, size = r[0], r[1], r[2], r[3], max(r[4], 0.1)
        for ln in reversed(lines[-4:]):
            if abs(ln[0] - y) <= 0.35 * min(size, ln[1]):
                ln[2].append(r)
                break
        else:
            lines.append([y, size, [r]])
    out = []
    for y, size, rs in lines:
        rs.sort(key=lambda r: r[1])
        segs: List[_Seg] = []
        cur = None
        last_x1 = None
        for text, x0, _y, x1, sz in rs:
            sz = max(sz, 0.1)
            if cur is not None:
                gap = x0 - last_x1
                if gap < -0.25 * sz:
                    flags.add("pdf_overlapping_text")      # text drawn over text
                if gap > 1.6 * sz:
                    segs.append(cur)
                    cur = None
                elif gap > 0.15 * sz and not cur.text[-1:].isspace() and \
                        not text[:1].isspace():
                    cur.text += " "
            if cur is None:
                cur = _Seg(text, x0, x1, y, sz)
            else:
                cur.text += text
                cur.x1 = max(cur.x1, x1)
                cur.size = max(cur.size, sz)
            last_x1 = x1 if last_x1 is None else max(last_x1, x1)
        if cur is not None:
            segs.append(cur)
        segs = [s for s in segs if s.text.strip()]
        for s in segs:
            s.text = " ".join(s.text.split()) if "\n" in s.text or "\t" in s.text else s.text.strip()
        if segs:
            out.append(segs)
    return out


def _key(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _furniture(pages_lines, boxes):
    """Indexes (page, line) of running headers / footers / page numbers."""
    marks = {}
    n = len(pages_lines)
    for pi, lines in enumerate(pages_lines):
        x0, y0, x1, y1 = boxes[pi]
        h = max(y1 - y0, 1.0)
        for li, segs in enumerate(lines):
            y = segs[0].y
            band = "top" if y > y1 - 0.08 * h else "bottom" if y < y0 + 0.08 * h else None
            if band is None:
                continue
            text = " ".join(s.text for s in segs)
            marks.setdefault((band, _key(text)), []).append((pi, li, text))
    out = set()
    for (band, key), hits in marks.items():
        pages = {h[0] for h in hits}
        for pi, li, text in hits:
            if _PAGE_NO.match(text) or (n >= 2 and len(pages) >= max(2, int(0.3 * n + 0.999))):
                out.add((pi, li))
    return out


def _columns(lines):
    """Column x-ranges from the segments of non-table lines (union of intervals);
    None when there is only one."""
    iv = sorted((s.x0, s.x1) for segs in lines for s in segs)
    if not iv:
        return None
    merged = [list(iv[0])]
    for a, b in iv[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    sizes = sorted(s.size for segs in lines for s in segs)
    med = sizes[len(sizes) // 2]
    cols = [merged[0]]
    for m in merged[1:]:
        if m[0] - cols[-1][1] >= 1.0 * med:
            cols.append(m)
        else:
            cols[-1][1] = max(cols[-1][1], m[1])
    return cols if len(cols) > 1 else None


def _prose(lines, idx, cols) -> bool:
    """Lines that fill most of their column's width are columns of prose, not a
    table (table cells rarely fill their column)."""
    if not cols:
        return False
    fills = []
    for i in idx:
        for sgm in lines[i]:
            for c in cols:
                if c[0] - 0.01 <= sgm.x0 and sgm.x1 <= c[1] + 0.01 and c[1] > c[0]:
                    fills.append((sgm.x1 - sgm.x0) / (c[1] - c[0]))
    fills.sort()
    return bool(fills) and fills[len(fills) // 2] >= 0.6


def _tables(lines, cols=None):
    """Indexes of lines that are table rows: >= 2 consecutive lines with >= 2
    segments whose starts align (within 3 points) in at least 2 places - unless
    they are clearly columns of prose."""
    found = set()
    rows = found
    run = []

    def aligned(a, b):
        hits = sum(1 for s in a for t in b if abs(s.x0 - t.x0) <= 3.0)
        return hits >= 2

    def close(run):
        if len(run) >= 2 and not _prose(lines, run, cols):
            rows.update(run)

    for i, segs in enumerate(lines):
        if len(segs) >= 2 and run and aligned(lines[run[-1]], segs):
            run.append(i)
        else:
            close(run)
            run = [i] if len(segs) >= 2 else []
    close(run)
    return rows


def _page_blocks(pi, lines, furniture, page_flags, body_size, box):
    """[(kind, text, bbox, level, marker_len)] for one page, in reading order."""
    flags = set(page_flags)
    content = [i for i in range(len(lines)) if (pi, i) not in furniture]
    table_rows = _tables(lines, _columns([lines[i] for i in content]))
    items = []                                  # (order key, kind, text, bbox, level, mlen)
    body = [i for i in content if i not in table_rows]
    cols = _columns([lines[i] for i in body])
    if cols and table_rows:
        flags.add("pdf_layout_uncertain")       # tables inside columns: order uncertain
    # furniture and table rows keep their own lines
    for i, segs in enumerate(lines):
        bb = (min(s.x0 for s in segs), min(s.y for s in segs), max(s.x1 for s in segs),
              max(s.y + s.size for s in segs))
        if (pi, i) in furniture:
            items.append(((0, -segs[0].y), "page_furniture", " ".join(s.text for s in segs), bb,
                          0, 0))
        elif i in table_rows:
            items.append(((0, -segs[0].y), "table_row", "\t".join(s.text for s in segs), bb,
                          0, 0))
    # body lines -> (column) -> segments
    col_lines = {}
    for i in body:
        segs = lines[i]
        if cols is None:
            if len(segs) > 1:
                flags.add("pdf_layout_uncertain")    # pieces far apart, no clear columns
                for k, s in enumerate(segs):
                    col_lines.setdefault(k, []).append(s)
            else:
                col_lines.setdefault(0, []).append(segs[0])
        else:
            for s in segs:
                ci = [k for k, c in enumerate(cols) if c[0] - 0.01 <= s.x0 and s.x1 <= c[1] + 0.01]
                if len(ci) != 1:
                    flags.add("pdf_layout_uncertain")
                    ci = [min(range(len(cols)), key=lambda k: abs(cols[k][0] - s.x0))]
                col_lines.setdefault(ci[0], []).append(s)
    for ci in sorted(col_lines):
        segs = sorted(col_lines[ci], key=lambda s: -s.y)
        para: List[_Seg] = []

        def flush():
            if not para:
                return
            first = para[0]
            heading = (len(para) == 1 and first.size >= 1.25 * body_size and
                       len(first.text.split()) <= 15)
            m = _BULLET.match(first.text) if not heading else None
            parts = []
            for k, s in enumerate(para):
                if k:
                    parts.append("\n" if parts and parts[-1].endswith("-") else " ")
                parts.append(s.text)
            text = "".join(parts)
            bb = (min(s.x0 for s in para), min(s.y for s in para), max(s.x1 for s in para),
                  max(s.y + s.size for s in para))
            kind = "heading" if heading else "list_item" if m else "paragraph"
            level = 1 if heading and first.size >= 1.6 * body_size else 2 if heading else 0
            items.append(((1 + ci, -first.y), kind, text, bb, level, m.end() if m else 0))
            para.clear()

        for s in segs:
            if para:
                prev = para[-1]
                gap = prev.y - s.y
                new = (gap > 1.6 * max(prev.size, s.size) or abs(prev.size - s.size) >
                       0.15 * max(prev.size, s.size) or _BULLET.match(s.text) is not None or
                       (prev.size >= 1.25 * body_size))
                if new:
                    flush()
            para.append(s)
        flush()
    # reading order: one column -> top to bottom (furniture and tables in place);
    # columns -> top furniture, each column top to bottom, tables, bottom furniture
    mid = (box[1] + box[3]) / 2.0
    if cols is None:
        items.sort(key=lambda it: it[0][1])
    else:
        def group(it):
            if it[1] == "page_furniture":
                return 0 if it[3][1] >= mid else 99
            if it[1] == "table_row":
                return 50
            return it[0][0]
        items.sort(key=lambda it: (group(it), it[0][1]))
    return items, flags


def layout(result: dict) -> tuple:
    """(canonical text, blocks, source flags) from the reader's checked result."""
    pages = result["pages"]
    page_lines = []
    page_flags = []
    for p in pages:
        flags = set(p["flags"])
        runs = [[unicodedata.normalize("NFC", r[0]), r[1], r[2], r[3], r[4]] for r in p["runs"]]
        page_lines.append(_lines(runs, flags) if p["kind"] == "text" else [])
        page_flags.append(flags)
    furniture = _furniture(page_lines, [p["box"] for p in pages])
    sizes = sorted(s.size for lines in page_lines for segs in lines for s in segs)
    body = sizes[len(sizes) // 2] if sizes else 10.0
    text_parts: List[str] = []
    pos = 0
    blocks: List[Block] = []
    path = {}
    for pi, p in enumerate(pages):
        items, flags = _page_blocks(pi, page_lines[pi], furniture, page_flags[pi], body,
                                    p["box"])
        blocking = tuple(sorted(f for f in flags if f.startswith("pdf_")))
        for _, kind, text, bb, level, mlen in items:
            text = canonical(text)
            if not text.strip():
                continue
            if text_parts:
                text_parts.append("\n\n")
                pos += 2
            start = pos
            text_parts.append(text)
            pos += len(text)
            s, e = start + mlen, pos
            seg = text[mlen:]
            lead = len(seg) - len(seg.lstrip())
            trail = len(seg) - len(seg.rstrip())
            s, e = s + lead, e - trail
            if s >= e:
                continue
            if kind == "heading":
                path = {k: v for k, v in path.items() if k < level}
                path[level] = text.strip()
            hp = " > ".join(path[k] for k in sorted(path))
            blocks.append(Block(kind, s, e, hp, level if kind == "heading" else 0,
                                page=pi + 1, bbox=tuple(round(v, 2) for v in bb),
                                flags=blocking))
    text = "".join(text_parts)
    if canonical(text) != text:
        raise ImportRefused("The PDF's text could not be placed exactly - nothing was imported.")
    info = list(result.get("info", []))
    scanned = [p["n"] for p in pages if p["kind"] == "image_only"]
    if scanned:
        info.append("pdf_image_only_pages:" + ",".join(str(n) for n in scanned))
    reference = sorted({b.page for b in blocks if b.flags})
    if reference:
        info.append("pdf_reference_only_pages:" + ",".join(str(n) for n in reference))
    info.append("pdf_pages:%d" % len(pages))
    return text, blocks, tuple(["pdf"] + sorted(set(info)))


def parse_pdf(data: bytes, timeout: float = TIMEOUT) -> Parsed:
    """PDF bytes -> Parsed. Refused (ImportRefused, nothing stored) for anything
    SK cannot read safely: not a PDF, damaged, encrypted (also permission-only),
    empty, too many pages / too complex, scanned (OCR not supported), no text."""
    result = read_in_child(data, timeout)
    text, blocks, flags = layout(result)
    if not blocks:
        raise ImportRefused("The PDF holds no text SK can read - nothing was imported.")
    from .importer import _check_blocks
    _check_blocks(text, blocks)
    return Parsed(text=text, fmt="pdf", blocks=tuple(blocks),
                  bytes_sha=hashlib.sha256(data).hexdigest(),
                  text_sha=hashlib.sha256(text.encode("utf-8")).hexdigest(), flags=flags)


def page_of(structure: str, start: int, end: int) -> Optional[dict]:
    """The page (and box) of the block holding [start, end) - from a stored
    version's structure; None when it is not one block of a PDF page."""
    try:
        blocks = json.loads(structure or "[]")
    except ValueError:
        return None
    for b in blocks:
        if len(b) >= 6 and b[1] <= start and end <= b[2] and b[5]:
            return {"page": b[5], "bbox": b[6] if len(b) > 6 else []}
    return None
