"""B6 PDF import: extraction accuracy, page references, refusals that store
nothing, misleading / hidden text that can never support, evidence rules on
clean pages, sharing / withdrawal / purging and privacy.

Synthetic PDFs only (tests/b6_pdf_maker.py). No AI, no network. The reader runs
in its own process (pypdf), so every parse costs a few tenths of a second."""
import concurrent.futures
import hashlib
import io
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import b6_pdf_maker as M  # noqa: E402
from b6_pdf_maker import Page, image, make  # noqa: E402
from puzzle_assistant.brain import importer  # noqa: E402
from puzzle_assistant.brain import passages as PS  # noqa: E402
from puzzle_assistant.brain import pdf_import  # noqa: E402
from puzzle_assistant.brain.importer import ImportRefused, parse  # noqa: E402
from puzzle_assistant.brain.store import BrainError  # noqa: E402
from tests.test_brain_b6 import Q_MG, World, all_files, where  # noqa: E402

MG = "Mira maintains the Lantern Garden."
KAI = "Kai maintains the Lantern Garden."
LIO = "Lio runs the Rope Shed."
Q_LIO = "Who runs the Rope Shed?"
CALM = "The harbour is calm today."
CAPTION = "The crew list for the whole harbour is given below, as follows."


def P(data):
    return parse(data, "pdf")


def btext(p, b):
    return p.text[b.start:b.end]


def one(*strings, **kw):
    return make([Page("".join(strings), **kw)])


def raw5(x, y, s, size=12):
    """Text in font F5: codes 65.. are the glyph names M i r a space m n t s h e L G d period."""
    order = "Mira mntsheLGd."
    return "BT /F5 %s Tf %s %s Td (%s) Tj ET\n" % (
        size, x, y, "".join(chr(65 + order.index(c)) for c in s))


def encrypt(data, algorithm, user="pw", permissions_only=False):
    from pypdf import PdfReader, PdfWriter
    from pypdf.constants import UserAccessPermissions
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    if permissions_only:
        w.encrypt("", "owner-pw", permissions_flag=UserAccessPermissions.PRINT,
                  algorithm=algorithm)
    else:
        w.encrypt(user, "owner-pw", algorithm=algorithm)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


class PdfWorld(World):
    def pdf(self, s, data, name="doc.pdf", **kw):
        kw.setdefault("author_kind", "named")
        kw.setdefault("author_name", "Ada Guide")
        kw.setdefault("publisher", "Harbour Press")
        return s.import_file(self.write(name, "", raw=data), **kw)["source_id"]

    def counts(self, s):
        q = s.store._db.execute
        return tuple(q("SELECT COUNT(*) FROM main." + t).fetchone()[0] for t in (
            "sources", "source_versions", "passages", "passage_facts", "source_files"))

    def snapshot(self):
        out = {}
        for p in all_files(self.files):
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def rows(self, s, sid):
        """[(kind, flags, text)] of the current version's passages."""
        return [(r[0], json.loads(r[1]), r[2]) for r in s.store._db.execute(
            "SELECT p.kind, p.flags, substr(v.text, p.start_pos + 1, p.end_pos - p.start_pos) "
            "FROM main.passages p JOIN main.source_versions v ON v.id=p.version_id "
            "WHERE p.source_id=? AND v.state='current' ORDER BY p.ordinal", (sid,))]

    def facts(self, s, sid):
        return s.store._db.execute(
            "SELECT COUNT(*) FROM main.passage_facts f JOIN main.passages p ON "
            "p.id=f.passage_id WHERE p.source_id=?", (sid,)).fetchone()[0]

    def blocks(self, s, sid):
        st = s.store._db.execute("SELECT structure FROM main.source_versions WHERE source_id=? "
                                 "AND state='current'", (sid,)).fetchone()[0]
        return json.loads(st)

    def refused(self, s, data, name="r.pdf", **kw):
        """Importing `data` must raise ImportRefused and store / change nothing."""
        before, files = self.counts(s), self.snapshot()
        path = self.write(name, "", raw=data)
        files = self.snapshot()
        with self.assertRaises(ImportRefused) as cm:
            s.import_file(path, author_kind="unknown")
        self.assertEqual(self.counts(s), before)
        self.assertEqual(self.snapshot(), files)
        return str(cm.exception)

    def e2e_flagged(self, data, quote=MG):
        """Import, learn MG, cite `quote`: never supports; page 2 (LIO) still does."""
        s = self.open("ana")
        sid = self.pdf(s, data)
        c = s.learn(MG, "imported", "Lantern Garden")
        try:
            r = s.cite(c, sid, quote)
            self.assertFalse(r["supports"])
        except BrainError:
            pass                                  # the text was dropped / altered: not quotable
        self.assertNotEqual(s.respond(Q_MG).category, "documented")
        self.assertEqual(s.respond(Q_MG).challenges, [])
        self.assertTrue(all("Lantern" not in x["text"] for x in s.suggestions()))
        for kind, flags, t in self.rows(s, sid):
            if "Rope" not in t:
                self.assertTrue(flags, (kind, t))
        c2 = s.learn(LIO, "imported", "Rope Shed")
        r2 = s.cite(c2, sid, LIO)
        self.assertTrue(r2["supports"], r2)
        self.assertEqual(r2["page"], 2)
        self.assertEqual(s.respond(Q_LIO).category, "documented")
        return s, sid


# ======================================================================== 1. extraction
class Extraction(unittest.TestCase):
    def test_single_page(self):
        p = P(one(M.text(72, 700, MG)))
        self.assertEqual(p.text, MG)
        self.assertEqual([(b.kind, b.page, b.flags) for b in p.blocks], [("paragraph", 1, ())])
        self.assertEqual(p.flags, ("pdf", "pdf_pages:1"))
        self.assertEqual(p.blocks[0].bbox[:2], (72.0, 700.0))

    def test_multi_page_in_page_order(self):
        sents = [MG, LIO, "Kai keeps the Salt Store.", "Nia mends the Blue Nets."]
        p = P(make([Page(M.text(72, 700, x)) for x in sents]))
        self.assertEqual(p.text, "\n\n".join(sents))
        self.assertEqual([(btext(p, b), b.page) for b in p.blocks],
                         [(x, i + 1) for i, x in enumerate(sents)])
        self.assertIn("pdf_pages:4", p.flags)

    def test_wrapped_paragraph_is_joined_with_one_space(self):
        rows = ["Mira maintains the Lantern", "Garden every day of the", "year, said the keeper."]
        p = P(one(M.lines(72, 700, rows)))
        self.assertEqual(p.text, " ".join(rows))
        self.assertEqual(len(p.blocks), 1)
        self.assertNotIn("  ", p.text)

    def test_paragraph_breaks_are_kept(self):
        a = ["The harbour opens early", "in the morning."]
        b = ["Lamps are lit at dusk", "by the keeper."]
        p = P(one(M.lines(72, 700, a), M.lines(72, 640, b)))
        self.assertEqual(p.text, "The harbour opens early in the morning.\n\n"
                                 "Lamps are lit at dusk by the keeper.")
        self.assertEqual([b.kind for b in p.blocks], ["paragraph", "paragraph"])

    def test_line_end_hyphen_is_kept_and_a_dehyphenated_quote_is_refused(self):
        rows = ["The harbour opens early, said Mira main-", "tains the Lantern Garden."]
        data = one(M.lines(72, 700, rows))
        p = P(data)
        self.assertEqual(p.text, "The harbour opens early, said Mira main-\ntains the Lantern "
                                 "Garden.")
        self.assertIn("main-\ntains", p.text)
        self.assertNotIn("maintains", p.text)

    def test_headings_become_heading_blocks(self):
        body = ["Lamps are lit at dusk by the keeper."]
        p = P(one(M.text(72, 740, "Harbour Guide", 24), M.text(72, 690, "Lamps", 16),
                  M.lines(72, 650, body), M.lines(72, 600, ["Boats leave at dawn."]),
                  M.lines(72, 550, ["Nets dry on the pier at noon."])))
        kinds = [(b.kind, btext(p, b)) for b in p.blocks]
        self.assertEqual(kinds[0], ("heading", "Harbour Guide"))
        self.assertEqual(kinds[1], ("heading", "Lamps"))
        self.assertEqual({k for k, _ in kinds[2:]}, {"paragraph"})
        self.assertEqual(p.blocks[0].level, 1)
        self.assertEqual(p.blocks[1].level, 2)
        self.assertEqual(p.blocks[2].heading_path, "Harbour Guide > Lamps")

    def test_bullet_items_exclude_the_marker(self):
        rows = ["- first item here", "- second item here", "1. third item here",
                "• fourth item here"]
        ops = "".join(M.text(72, 700 - 30 * i, r) for i, r in enumerate(rows))
        p = P(one(ops))
        items = [(b.kind, btext(p, b)) for b in p.blocks]
        self.assertEqual(items, [("list_item", "first item here"),
                                 ("list_item", "second item here"),
                                 ("list_item", "third item here"),
                                 ("list_item", "fourth item here")])
        self.assertIn("- first item here", p.text)             # the marker stays in the text

    def test_two_columns_are_read_left_then_right(self):
        left = ["Harbour tide notes one", "Lamps are lit at dusk", "Mira keeps the oil full"]
        right = ["Rope shed rules apply", "Lio runs the Rope Shed", "Knots are checked daily"]
        p = P(one(M.lines(72, 700, left, leading=14), M.lines(330, 700, right, leading=14)))
        self.assertEqual(p.text, " ".join(left) + "\n\n" + " ".join(right))
        self.assertEqual([b.flags for b in p.blocks], [(), ()])

    def test_two_columns_with_several_paragraphs(self):
        # prose lines (sentences' worth of words that fill their column)
        l1, l2 = ["Left one goes on here", "left one ends here now"], \
            ["Left two goes on here", "left two ends here now"]
        r1, r2 = ["Right one goes on here", "right one ends here now"], \
            ["Right two goes on here", "right two ends here now"]
        ops = (M.lines(72, 700, l1, leading=14) + M.lines(72, 640, l2, leading=14) +
               M.lines(330, 700, r1, leading=14) + M.lines(330, 640, r2, leading=14))
        p = P(one(ops))
        self.assertEqual(p.text, "Left one goes on here left one ends here now\n\n"
                                 "Left two goes on here left two ends here now\n\n"
                                 "Right one goes on here right one ends here now\n\n"
                                 "Right two goes on here right two ends here now")
        self.assertTrue(all(not b.flags for b in p.blocks))

    def test_columns_of_short_pieces_can_never_support(self):
        # review fix: short pieces side by side may be a table read as columns -
        # whatever they are read as, none of it can ever support
        l1, l2 = ["Left one a", "left one b"], ["Left two a", "left two b"]
        r1, r2 = ["Right one a", "right one b"], ["Right two a", "right two b"]
        ops = (M.lines(72, 700, l1, leading=14) + M.lines(72, 640, l2, leading=14) +
               M.lines(330, 700, r1, leading=14) + M.lines(330, 640, r2, leading=14))
        p = P(one(ops))
        for x in PS.split(p):
            self.assertTrue(x.kind not in PS.SUPPORT_KINDS or x.flags, (x, p.text))

    TABLE = (("Name", "Role", "Zone"), ("Mira", "Keeper", "North"), ("Lio", "Cook", "South"),
             ("Kai", "Sailor", "East"))

    def table_ops(self, rows, top=700, xs=(72, 250, 400)):
        return "".join(M.text(x, top - 14 * i, c) for i, r in enumerate(rows)
                       for x, c in zip(xs, r))

    def test_table_rows_are_table_blocks(self):
        p = P(one(M.text(72, 740, CAPTION), self.table_ops(self.TABLE)))
        kinds = [b.kind for b in p.blocks]
        self.assertEqual(kinds, ["paragraph"] + ["table_row"] * 4)
        self.assertEqual(btext(p, p.blocks[2]), "Mira\tKeeper\tNorth")

    def test_a_table_is_never_evidence(self):
        p = P(one(M.text(72, 740, CAPTION), self.table_ops(self.TABLE)))
        got = PS.split(p)
        tab = [x for x in got if x.kind == "table_row"]
        self.assertEqual(len(tab), 4)
        self.assertTrue(all(x.kind not in PS.SUPPORT_KINDS for x in tab))

    def test_columns_of_short_unaligned_lines_are_layout_uncertain(self):
        # not a table (no shared baselines), but columns of short pieces: reference only
        ops = (M.lines(72, 700, ["Mira keeps it", "Kai sails", "Lio cooks"], leading=14) +
               M.lines(330, 693, ["Nora reads", "Ines rows", "Bo runs"], leading=14))
        p = P(one(ops))
        self.assertTrue(p.blocks)
        for b in p.blocks:
            self.assertIn("pdf_layout_uncertain", b.flags, btext(p, b))

    def test_a_table_alone_or_under_a_short_caption_is_still_a_table(self):
        # found by this suite (fixed): with no wide prose line spanning the table, its
        # columns were taken for columns of prose and read as unflagged paragraphs.
        rows = (("Name", "Role", "Zone"), ("Mira", "Cook", "Dock"), ("Lio", "Chef", "Pier"))
        for name, ops in (("alone", self.table_ops(rows)),
                          ("short caption", M.text(72, 740, "The crew list.") +
                           self.table_ops(rows))):
            with self.subTest(name):
                p = P(one(ops))
                self.assertEqual([b.kind for b in p.blocks if b.kind != "paragraph" or
                                  "crew" not in btext(p, b)], ["table_row"] * 3)

    def test_running_headers_footers_and_page_numbers_are_furniture(self):
        def pg(n):
            return Page(M.text(72, 760, "Harbour Guide") + M.text(72, 700, "Body number %s." % n)
                        + M.text(300, 30, "Page %d" % n) + M.text(72, 40, "Footer line"))
        p = P(make([pg(1), pg(2), pg(3)]))
        kinds = [(b.kind, btext(p, b)) for b in p.blocks if b.page == 2]
        self.assertEqual(sorted(k for k, _ in kinds),
                         ["page_furniture"] * 3 + ["paragraph"])
        self.assertEqual({btext(p, b) for b in p.blocks if b.kind == "page_furniture"},
                         {"Harbour Guide", "Footer line", "Page 1", "Page 2", "Page 3"})
        got = PS.split(p)
        self.assertEqual(len(got), 3)                              # the three body lines only
        self.assertTrue(all(p.text[x.start:x.end].startswith("Body") for x in got))

    def test_type0_font_with_tounicode_decodes_exactly(self):
        s = "Mira maintains the Lantern Garden near the café ñandú."
        p = P(one(M.text(72, 700, s, font="F2")))
        self.assertEqual(p.text, s)
        self.assertEqual(p.blocks[0].flags, ())

    def test_differences_glyph_names_decode_exactly(self):
        p = P(one(raw5(72, 700, MG)))
        self.assertEqual(p.text, MG)
        self.assertEqual(p.blocks[0].flags, ())

    def test_form_xobject_text_is_included(self):
        p = P(one(M.text(72, 700, MG), "q 1 0 0 1 100 400 cm /Fm1 Do Q\n"))
        self.assertEqual(p.text, MG + "\n\nForm text.")
        self.assertEqual(p.blocks[1].bbox[0], 105.0)
        self.assertEqual(p.blocks[1].flags, ())

    def test_random_round_trip(self):
        words = ("harbour lantern garden rope shed boat tide lamp oil keeper north pier dock net "
                 "knot sail wind calm bell quay mast gull salt fog").split()
        rnd = random.Random(20240607)
        docs = []
        for _ in range(300):
            pages = []
            for _p in range(rnd.randint(1, 4)):
                sents = []
                for _s in range(rnd.randint(1, 5)):
                    w = [rnd.choice(words) for _ in range(rnd.randint(3, 7))]
                    sents.append(w[0].capitalize() + " " + " ".join(w[1:]) + ".")
                pages.append(sents)
            docs.append(pages)

        def build(pages):
            out = []
            for sents in pages:
                y = 700.0
                ops = []
                for t in sents:
                    ops.append(M.text(72, round(y, 2), t))
                    y -= rnd_gap.pop()
                out.append(Page("".join(ops)))
            return make(out)

        rnd_gap = []
        datas = []
        for pages in docs:
            rnd_gap[:] = [rnd.randint(40, 90) for _ in range(40)]
            datas.append(build(pages))
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            parsed = list(ex.map(P, datas))
        for k, (pages, p) in enumerate(zip(docs, parsed)):
            flat = [(i + 1, t) for i, sents in enumerate(pages) for t in sents]
            self.assertEqual(p.text, "\n\n".join(t for _, t in flat), k)
            self.assertEqual([(b.page, btext(p, b)) for b in p.blocks], flat, k)
            self.assertTrue(all(b.kind == "paragraph" and not b.flags for b in p.blocks), k)
            for x in PS.split(p):
                self.assertEqual(p.text[x.start:x.end], p.text[x.start:x.end])
                inside = [b for b in p.blocks if b.start <= x.start and x.end <= b.end]
                self.assertEqual(len(inside), 1, (k, x))
                self.assertEqual(p.text[x.start:x.end], btext(p, inside[0]))


# ======================================================================== 2. page references
class PageReferences(PdfWorld):
    SENTS = ["Mira maintains the Lantern Garden.", "Lio runs the Rope Shed.",
             "Kai keeps the Salt Store.", "Nia mends the Blue Nets."]

    def test_every_block_reports_its_page(self):
        p = P(make([Page(M.text(72, 700, t) + M.text(72, 600, "Extra line %s here." % w))
                    for t, w in zip(self.SENTS, "abcd")]))
        for b in p.blocks:
            want = 1 + self.SENTS.index(btext(p, b)) if btext(p, b) in self.SENTS else \
                1 + "abcd".index(btext(p, b).split()[2])
            self.assertEqual(b.page, want, btext(p, b))
        self.assertEqual(sorted({b.page for b in p.blocks}), [1, 2, 3, 4])

    def test_cite_returns_page_and_bbox(self):
        s = self.open("ana")
        data = make([Page(M.text(72, 700, "Opening page text here.")),
                     Page(M.text(100, 500, MG)), Page(M.text(72, 700, LIO))])
        sid = self.pdf(s, data)
        c = s.learn(MG, "imported", "Lantern Garden")
        r = s.cite(c, sid, MG)
        self.assertTrue(r["supports"])
        self.assertEqual(r["page"], 2)
        x0, y0, x1, y1 = r["bbox"]
        self.assertTrue(x0 <= 100 <= x1 and y0 <= 505 <= y1, r["bbox"])
        c2 = s.learn(LIO, "imported", "Rope Shed")
        r2 = s.cite(c2, sid, LIO)
        self.assertEqual(r2["page"], 3)
        self.assertAlmostEqual(M.width(LIO) + 72, r2["bbox"][2], places=1)

    def test_search_passages_carry_the_page(self):
        s = self.open("ana")
        self.pdf(s, make([Page(M.text(72, 700, t)) for t in self.SENTS]))
        for i, (q, t) in enumerate(zip(("lantern garden", "rope shed", "salt store",
                                        "blue nets"), self.SENTS)):
            hits = s.search_passages(q)
            self.assertTrue(hits, q)
            self.assertEqual(hits[0]["text"], t)
            self.assertEqual(hits[0]["page"], i + 1)

    def test_a_sentence_cannot_be_quoted_across_a_page_boundary(self):
        s = self.open("ana")
        data = make([Page(M.text(72, 150, "The keeper said that Mira maintains the")),
                     Page(M.text(72, 700, "Lantern Garden. Boats leave at dawn."))])
        p = P(data)
        self.assertEqual([b.page for b in p.blocks], [1, 2])
        sid = self.pdf(s, data)
        c = s.learn(MG, "imported", "Lantern Garden")
        for q in (MG, "Mira maintains the\n\nLantern Garden.", "Mira maintains the Lantern",
                  "maintains the Lantern Garden."):
            with self.subTest(q=q):
                with self.assertRaises(BrainError):
                    s.cite(c, sid, q)
        self.assertNotEqual(s.respond(Q_MG).category, "documented")

    def test_no_passage_crosses_a_block_or_a_page(self):
        s = self.open("ana")
        text = ("Mira keeps the oil full. Lio runs the Rope Shed. It opens at nine. "
                "Boats leave at dawn.")
        data = make([Page(M.lines(72, 700, [text[:40], text[40:]])),
                     Page(M.text(72, 700, "Kai mends nets. Nia sails at noon."))])
        sid = self.pdf(s, data)
        blocks = self.blocks(s, sid)
        n = 0
        for pid, st, en in s.store._db.execute("SELECT id, start_pos, end_pos FROM main.passages "
                                               "WHERE source_id=?", (sid,)):
            inside = [b for b in blocks if b[1] <= st and en <= b[2]]
            self.assertEqual(len(inside), 1, (st, en))
            self.assertTrue(inside[0][5] in (1, 2))
            n += 1
        self.assertGreaterEqual(n, 6)
        self.assertTrue(all(len(b) == 8 for b in blocks))


# ======================================================================== 3. refusals
class Refusals(PdfWorld):
    GOOD = None

    @classmethod
    def good(cls):
        return one(M.text(72, 700, MG))

    def test_damaged_and_non_pdf_files_store_nothing(self):
        s = self.open("ana")
        good = self.good()
        cases = {
            "not a pdf": b"Mira maintains the Lantern Garden. This is plain text.",
            "empty": b"",
            "truncated half": good[:len(good) // 2],
            "truncated tail": good[:-25],
            "bad xref": good.replace(b"startxref\n", b"startxref\n9", 1),
            "header only": b"%PDF-1.7\n",
            "zip": b"PK\x03\x04" + b"\x00" * 60,
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(s, data, "bad.pdf")

    def test_zero_pages_and_too_many_pages(self):
        s = self.open("ana")
        with self.subTest("zero"):
            self.assertIn("no pages", self.refused(s, make([])))
        with self.subTest("301"):
            msg = self.refused(s, make([Page(M.text(72, 700, MG))] * 301))
            self.assertIn("301", msg)
        with self.subTest("300 pages are fine"):
            p = P(make([Page(M.text(72, 700, "Page text %s." % "x"))] * 300))
            self.assertIn("pdf_pages:300", p.flags)

    def test_more_than_25_mb_is_refused_before_the_pdf_is_read(self):
        big = b"%PDF-1.7\n" + b"x" * importer.PDF_MAX_BYTES                 # one byte too many
        self.assertGreater(len(big), importer.PDF_MAX_BYTES)
        with mock.patch.object(pdf_import, "read_in_child",
                               side_effect=AssertionError("the PDF was read")):
            with self.assertRaises(ImportRefused):
                parse(big, "pdf")
            with self.assertRaises(ImportRefused):
                parse(bytearray(big), "pdf")
        s = self.open("ana")
        with mock.patch.object(pdf_import, "read_in_child",
                               side_effect=AssertionError("the PDF was read")):
            self.refused(s, big, "big.pdf")

    def test_encrypted_pdfs_are_refused(self):
        good = self.good()
        s = self.open("ana")
        for alg in ("RC4-40", "RC4-128", "AES-128", "AES-256-R5", "AES-256"):
            for perm_only in (False, True):
                with self.subTest(alg=alg, permissions_only=perm_only):
                    try:
                        data = encrypt(good, alg, permissions_only=perm_only)
                    except Exception as exc:                    # pypdf DependencyError
                        if "DependencyError" in type(exc).__name__:
                            self.skipTest("cryptography not installed")
                        raise
                    self.assertIn(b"/Encrypt", data)
                    msg = self.refused(s, data, "enc.pdf")
                    self.assertIn("encrypted", msg)

    def test_decompression_bomb(self):
        s = self.open("ana")
        ops = M.text(72, 700, MG) + " " * 60_000_000
        data = make([Page(ops)])
        self.assertLess(len(data), 1_000_000)
        t = time.time()
        self.refused(s, data, "bomb.pdf")
        self.assertLess(time.time() - t, 60)

    def test_scanned_pdfs_are_refused_with_an_ocr_message(self):
        s = self.open("ana")
        msg = self.refused(s, make([Page(image(0, 0, 612, 792)), Page(image(0, 0, 612, 792))]))
        self.assertIn("OCR", msg)

    def test_only_invisible_text_without_images_has_no_text(self):
        s = self.open("ana")
        msg = self.refused(s, one("3 Tr\n", M.text(72, 700, MG)))
        self.assertIn("no text", msg.lower())

    def test_empty_pages_have_no_text(self):
        s = self.open("ana")
        self.refused(s, make([Page(""), Page("")]))

    def test_the_reader_that_hangs_is_stopped(self):
        data = self.good()
        with mock.patch.object(pdf_import, "_BOOT", "import time; time.sleep(60)"):
            t = time.time()
            with self.assertRaises(ImportRefused) as cm:
                pdf_import.parse_pdf(data, timeout=3)
        self.assertLess(time.time() - t, 20)
        self.assertIn("too long", str(cm.exception))

    def test_the_reader_that_crashes_or_is_missing(self):
        data = self.good()
        s = self.open("ana")
        for name, boot in (("exit 3", "import os; os._exit(3)"),
                           ("exception", "raise SystemExit('boom')"),
                           ("segfault-like", "import os, signal; os.kill(os.getpid(), 9)"),
                           ("exit 0, no output", "pass")):
            with self.subTest(name):
                with mock.patch.object(pdf_import, "_BOOT", boot):
                    with self.assertRaises(ImportRefused):
                        parse(data, "pdf")
                    self.refused(s, data, "crash.pdf")

    def test_the_reader_that_lies_is_refused(self):
        data = self.good()
        s = self.open("ana")
        page = {"n": 1, "kind": "text", "box": [0, 0, 612, 792], "flags": [],
                "runs": [["Hello harbour.", 72, 700, 150, 12]]}

        def js(**over):
            pg = dict(page, **over)
            return "import sys; sys.stdout.write(%r)" % json.dumps({"pages": [pg], "info": []})

        lies = {
            "invalid json": "import sys; sys.stdout.write('{nope')",
            "a list": "import sys; sys.stdout.write('[1, 2]')",
            "pages not a list": "import sys; sys.stdout.write('{\"pages\": 5}')",
            "no pages": "import sys; sys.stdout.write('{\"pages\": [], \"info\": []}')",
            "page is a string": "import sys; sys.stdout.write('{\"pages\": [\"x\"]}')",
            "short box": js(box=[0, 0, 612]),
            "wrong page number": js(n=2),
            "unknown kind": js(kind="weird"),
            "flag without pdf_ prefix": js(flags=["hidden"]),
            "flag not a string": js(flags=[3]),
            "run of 4": js(runs=[["x", 1, 2, 3]]),
            "run text is a number": js(runs=[[7, 1, 2, 3, 4]]),
            "run coordinate is a string": js(runs=[["x", "1", 2, 3, 4]]),
            "run coordinate is a bool": js(runs=[["x", True, 2, 3, 4]]),
            "runs not a list": js(runs="Hello"),
            "NaN coordinate": "import sys; sys.stdout.write('{\"pages\": [{\"n\": 1, \"kind\": "
                              "\"text\", \"box\": [0, 0, 612, NaN], \"flags\": [], \"runs\": []}]}')",
            "Infinity coordinate": "import sys; sys.stdout.write('{\"pages\": [{\"n\": 1, "
                                   "\"kind\": \"text\", \"box\": [0, 0, 612, 792], \"flags\": [], "
                                   "\"runs\": [[\"x\", 1, 2, Infinity, 4]]}]}')",
            "301 pages": "import json, sys; sys.stdout.write(json.dumps({'pages': [{'n': i, "
                         "'kind': 'empty', 'box': [0, 0, 1, 1], 'flags': [], 'runs': []} "
                         "for i in range(1, 302)]}))",
            "info not a list": "import sys; sys.stdout.write('{\"pages\": [{\"n\": 1, \"kind\": "
                               "\"text\", \"box\": [0,0,1,1], \"flags\": [], \"runs\": []}], "
                               "\"info\": 5}')",
            "non-ascii output": "import sys; sys.stdout.buffer.write('\\u00e9'.encode())",
            "refused with no message": "import sys; sys.stdout.write('{\"refused\": \"x\"}')",
        }
        for name, boot in lies.items():
            with self.subTest(name):
                with mock.patch.object(pdf_import, "_BOOT", boot):
                    with self.assertRaises(ImportRefused):
                        parse(data, "pdf")
        with self.subTest("the harness itself works: an honest answer is accepted"):
            with mock.patch.object(pdf_import, "_BOOT", js()):
                got = parse(data, "pdf")
            self.assertEqual(got.text, "Hello harbour.")
        with self.subTest("library: nothing stored"):
            for boot in (lies["invalid json"], lies["wrong page number"]):
                with mock.patch.object(pdf_import, "_BOOT", boot):
                    self.refused(s, data, "lie.pdf")

    def test_the_reader_runs_isolated(self):
        real = subprocess.run
        calls = []

        def spy(*a, **k):
            calls.append((a, k))
            return real(*a, **k)
        data = self.good()
        with mock.patch.object(pdf_import.subprocess, "run", spy):
            P(data)
        self.assertEqual(len(calls), 1)
        (argv,), kw = calls[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], "-I")
        self.assertEqual(kw["input"], data)
        self.assertTrue(set(kw["env"]) <= {"SYSTEMROOT", "PATH", "TEMP", "TMP", "HOME", "LANG"})
        self.assertIn("timeout", kw)

    def test_the_brain_process_never_imports_pypdf(self):
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from puzzle_assistant.brain import importer\n"
            "data = sys.stdin.buffer.read()\n"
            "p = importer.parse(data, 'pdf')\n"
            "print(json.dumps([p.text, [m for m in sys.modules if m.split('.')[0] in "
            "('pypdf', 'PyPDF2') or m.endswith('pdf_reader')]]))\n" % ROOT)
        r = subprocess.run([sys.executable, "-c", script], input=self.good(),
                           capture_output=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[-500:])
        text, mods = json.loads(r.stdout.decode())
        self.assertEqual(text, MG)
        self.assertEqual(mods, [])
        self.assertNotIn("pypdf", sys.modules.get("puzzle_assistant.brain.pdf_import").__dict__)

    def test_mixed_documents_import_text_pages_and_list_image_pages(self):
        s = self.open("ana")
        data = make([Page(M.text(72, 700, MG)), Page(image(0, 0, 612, 792)),
                     Page(M.text(72, 700, LIO)), Page(image(0, 0, 612, 792))])
        sid = self.pdf(s, data)
        flags = json.loads(s.versions(sid)[0]["flags"])
        self.assertIn("pdf_image_only_pages:2,4", flags)
        self.assertEqual({b[5] for b in self.blocks(s, sid)}, {1, 3})
        c = s.learn(LIO, "imported", "Rope Shed")
        r = s.cite(c, sid, LIO)
        self.assertTrue(r["supports"])
        self.assertEqual(r["page"], 3)

    def test_ocr_layer_is_reference_only(self):
        ops = ("q 612 0 0 792 0 0 cm /Im1 Do Q\n3 Tr\n" + M.text(72, 700, MG))
        data = make([Page(ops), Page(M.text(72, 700, LIO))])
        p = P(data)
        self.assertTrue(all("pdf_ocr_layer" in b.flags for b in p.blocks if b.page == 1))
        self.assertEqual([b.flags for b in p.blocks if b.page == 2], [()])
        self.assertIn("pdf_reference_only_pages:1", p.flags)
        s, sid = self.e2e_flagged(data)
        hits = s.search_passages("lantern garden")
        self.assertTrue(hits)
        self.assertFalse(hits[0]["can_support"])
        self.assertEqual(hits[0]["page"], 1)


# ======================================================================== 4. hidden text
def q(*ops):
    """Graphics-state changes stay inside q ... Q, so they do not leak to the next lines."""
    return "q\n" + "".join(ops) + "Q\n"


class MisleadingText(PdfWorld):
    NEUTRAL = M.text(72, 600, CALM)
    GS = "/GS1 << /ca 0.1 >>"

    def cases(self):
        T = M.text
        return {
            "white fill": (q("1 1 1 rg\n", T(72, 700, MG)), {}, {}, "pdf_hidden_text"),
            "white cmyk": (q("0 0 0 0 k\n", T(72, 700, MG)), {}, {}, "pdf_hidden_text"),
            "white gray": (q("1 g\n", T(72, 700, MG)), {}, {}, "pdf_hidden_text"),
            "partly outside the page": (T(500, 700, MG), {}, {}, "pdf_hidden_text"),
            "far-apart pieces without columns": (
                T(72, 700, "This long line runs right across the whole width of the page.") +
                T(72, 680, "Kai") + T(470, 680, "here."), {}, {}, "pdf_layout_uncertain"),
            "render mode 3 (no images)": (q("3 Tr\n", T(72, 700, MG)), {}, {},
                                          "pdf_hidden_text"),
            "outside the page (left)": (T(-500, 700, MG), {}, {}, "pdf_hidden_text"),
            "outside the page (above)": (T(72, 2000, MG), {}, {}, "pdf_hidden_text"),
            "outside a rectangular clip": (q("0 0 50 50 re W n\n", T(72, 700, MG)), {}, {},
                                           "pdf_hidden_text"),
            "complex clip": (q("72 690 m 300 690 l 300 720 l 72 720 l h "
                               "100 600 m 200 600 l 150 650 l h W n\n", T(72, 700, MG)), {}, {},
                             "pdf_complex_clip"),
            "triangular clip": (q("72 690 m 300 750 l 300 690 l h W n\n", T(72, 700, MG)), {}, {},
                                "pdf_complex_clip"),
            "painted over later": (T(72, 700, MG) + "1 1 1 rg\n60 690 300 40 re f\n", {}, {},
                                   "pdf_covered_text"),
            "transparent ExtGState": (q("/GS1 gs\n", T(72, 700, MG)), {"extgstate": self.GS}, {},
                                      "pdf_hidden_text"),
            "tiny text": (T(72, 700, MG, size=2), {}, {}, "pdf_tiny_text"),
            "squeezed text": (q("30 Tz\n", T(72, 700, MG)), {}, {}, "pdf_scaled_text"),
            "rotated text matrix": ("BT /F1 12 Tf 0 1 -1 0 72 700 Tm (%s) Tj ET\n" % MG, {}, {},
                                    "pdf_rotated_text"),
            "rotated page": (T(72, 700, MG), {}, {"rotate": 90}, "pdf_rotated_page"),
            "optional content": ("/OC /MC0 BDC\n" + T(72, 700, MG) + "EMC\n",
                                 {"properties": "/MC0 << /Type /OCG /Name (Layer) >>"}, {},
                                 "pdf_optional_content"),
            "Type3 font": (T(72, 700, "AAAA", font="F4"), {}, {}, "pdf_type3_font"),
            "TrueType without encoding": (T(72, 700, MG, font="F3"), {}, {},
                                          "pdf_uncertain_text"),
            "Type0 without ToUnicode": (T(72, 700, MG, font="F6"), {}, {}, "pdf_uncertain_text"),
            "a code its ToUnicode does not map": (T(72, 700, MG + "\ue000", font="F2"), {}, {},
                                                  "pdf_uncertain_text"),
            "ToUnicode that contradicts WinAnsi": (T(72, 700, MG, font="F7"), {}, {},
                                                   "pdf_uncertain_text"),
            "pattern colour": (q("/Pattern cs /P1 scn\n", T(72, 700, MG)), {}, {},
                               "pdf_uncertain_colour"),
            "overlapping text": (T(72, 700, MG) + T(80, 700, KAI), {}, {},
                                 "pdf_overlapping_text"),
            "unknown operator": ("1 2 3 ZZ\n" + T(72, 700, MG), {}, {}, "pdf_reader_warning"),
        }

    def test_flags_per_page(self):
        for name, (ops, kw, pkw, flag) in self.cases().items():
            with self.subTest(name):
                data = make([Page(ops + self.NEUTRAL, **pkw), Page(M.text(72, 700, LIO))], **kw)
                p = P(data)
                one_, two = ([b for b in p.blocks if b.page == n] for n in (1, 2))
                self.assertTrue(one_, "page 1 has blocks")
                for b in one_:
                    self.assertIn(flag, b.flags, (name, btext(p, b)))
                self.assertEqual([b.flags for b in two], [()])
                self.assertIn("pdf_reference_only_pages:1", p.flags)

    def test_end_to_end_no_support_but_the_clean_page_supports(self):
        for name, (ops, kw, pkw, flag) in self.cases().items():
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                data = make([Page(ops + self.NEUTRAL, **pkw), Page(M.text(72, 700, LIO))], **kw)
                s, sid = self.e2e_flagged(data)
                self.assertEqual(self.facts(s, sid) > 0, True)          # page 2 only
                for kind, flags, t in self.rows(s, sid):
                    if not flags:
                        self.assertEqual(t, LIO)

    def test_a_flagged_page_stores_no_facts(self):
        for name in ("white fill", "tiny text", "painted over later", "optional content"):
            with self.subTest(name):
                ops, kw, pkw, flag = self.cases()[name]
                s = self.open("ana")
                sid = self.pdf(s, make([Page(ops + self.NEUTRAL, **pkw)], **kw),
                               "f%s.pdf" % len(name))
                self.assertEqual(self.facts(s, sid), 0)
                self.assertTrue(self.rows(s, sid))

    def test_flagged_pages_are_searchable_labelled_reference(self):
        s = self.open("ana")
        sid = self.pdf(s, make([Page(q("1 1 1 rg\n", M.text(72, 700, MG)))]))
        hits = s.search_passages("lantern garden")
        self.assertTrue(hits)
        for h in hits:
            self.assertFalse(h["can_support"])
            self.assertIn("pdf_hidden_text", h["flags"])
            self.assertEqual(h["page"], 1)
            self.assertIn("reference only", h["label"])
        self.assertEqual(s.suggestions(), [])

    def test_a_background_rectangle_drawn_before_the_text_is_not_a_flag(self):
        ops = "0.9 0.9 0.5 rg\n60 690 300 40 re f\n0 g\n" + M.text(72, 700, MG)
        p = P(one(ops))
        self.assertEqual([b.flags for b in p.blocks], [()])
        self.assertEqual(p.flags, ("pdf", "pdf_pages:1"))
        s = self.open("ana")
        sid = self.pdf(s, one(ops))
        c = s.learn(MG, "imported", "Lantern Garden")
        self.assertTrue(s.cite(c, sid, MG)["supports"])
        self.assertEqual(s.respond(Q_MG).category, "documented")

    def test_a_stricter_state_does_not_leak_out_of_q_Q(self):
        p = P(one(q("30 Tz\n", M.text(72, 700, MG)), M.text(72, 600, CALM)))
        # the page is flagged (the squeezed text was seen) but no flag claims more than that
        self.assertTrue(all(b.flags == ("pdf_scaled_text",) for b in p.blocks))


# ======================================================================== 5. evidence
class CleanPdfEvidence(PdfWorld):
    def setUp(self):
        super().setUp()
        self.s = self.open("ana")

    def clean(self, sentence=MG, name="a.pdf", extra=()):
        data = make([Page(M.text(72, 700, sentence) + "".join(
            M.text(72, 640 - 40 * i, e) for i, e in enumerate(extra)))])
        return self.pdf(self.s, data, name)

    def test_cite_supports_and_the_answer_is_documented(self):
        sid = self.clean()
        c = self.s.learn(MG, "imported", "Lantern Garden")
        r = self.s.cite(c, sid, MG)
        self.assertTrue(r["supports"])
        self.assertEqual((r["page"], r["passage_kind"]), (1, "sentence"))
        a = self.s.respond(Q_MG)
        self.assertEqual((a.category, a.value), ("documented", "Mira"))

    def test_clean_pdf_behaves_like_txt(self):
        pdf_sid = self.clean(MG, "a.pdf")
        txt_sid = self.imp(self.s, MG + "\n", "a.txt")
        c = self.s.learn(MG, "imported", "Lantern Garden")
        a = self.s.cite(c, pdf_sid, MG)
        b = self.s.cite(c, txt_sid, MG)
        self.assertEqual((a["supports"], a["why"], a["passage_kind"]),
                         (b["supports"], b["why"], b["passage_kind"]))
        for q_ in ("Mira maintains the Lantern garden.", "Mira  maintains the Lantern Garden.",
                   "Kai maintains the Lantern Garden.", "Garden. Boats"):
            with self.assertRaises(BrainError, msg=q_):
                self.s.cite(c, pdf_sid, q_)

    def test_hedged_text_in_a_pdf_does_not_support_like_txt(self):
        sid = self.clean("Perhaps Mira maintains the Lantern Garden.")
        c = self.s.learn(MG, "imported", "Lantern Garden")
        self.assertFalse(self.s.cite(c, sid, MG)["supports"])

    def test_review_verified_makes_the_answer_established(self):
        sid = self.clean()
        c = self.s.learn(MG, "imported", "Lantern Garden")
        self.s.cite(c, sid, MG)
        self.s.review(c, "verified")
        self.assertEqual(self.s.respond(Q_MG).category, "established")

    def test_a_flagged_page_cannot_be_reviewed_as_verified(self):
        sid = self.pdf(self.s, one(q("1 1 1 rg\n", M.text(72, 700, MG))), "w.pdf")
        c = self.s.learn(MG, "imported", "Lantern Garden")
        self.assertFalse(self.s.cite(c, sid, MG)["supports"])
        with self.assertRaises(BrainError):
            self.s.review(c, "verified")

    def established(self):
        sid = self.clean()
        c = self.s.learn(MG, "imported", "Lantern Garden")
        self.s.cite(c, sid, MG)
        self.s.review(c, "verified")
        return c

    def test_a_clean_pdf_page_challenges_an_established_answer(self):
        self.established()
        self.clean(KAI, "k.pdf")
        a = self.s.respond(Q_MG)
        self.assertEqual((a.category, a.value), ("established", "Mira"))
        self.assertEqual(len(a.challenges), 1)
        self.assertEqual(a.challenges[0]["values"], ["Kai"])

    def test_a_flagged_page_never_challenges(self):
        self.established()
        data = make([Page(q("1 1 1 rg\n", M.text(72, 700, KAI))),
                     Page(q("30 Tz\n", M.text(72, 700, KAI))),
                     Page(M.text(72, 700, KAI, size=2))])
        sid = self.pdf(self.s, data, "k.pdf", publisher="Other")
        a = self.s.respond(Q_MG)
        self.assertEqual((a.category, a.challenges), ("established", []))
        self.assertEqual(self.facts(self.s, sid), 0)

    def test_a_clean_page_challenges_even_when_another_page_is_flagged(self):
        self.established()
        data = make([Page(q("1 1 1 rg\n", M.text(72, 700, "Lio maintains the Lantern Garden."))),
                     Page(M.text(72, 700, KAI))])
        self.pdf(self.s, data, "k.pdf", publisher="Other")
        a = self.s.respond(Q_MG)
        self.assertEqual(len(a.challenges), 1)
        self.assertEqual(a.challenges[0]["values"], ["Kai"])

    def test_suggestions_come_from_clean_pages_only(self):
        data = make([Page(q("1 1 1 rg\n", M.text(72, 700, KAI))),
                     Page(M.text(72, 700, MG)),
                     Page(M.text(72, 700, "Lio runs the Rope Shed.", size=2))])
        self.pdf(self.s, data)
        sug = self.s.suggestions()
        self.assertEqual(len(sug), 1)
        self.assertIn("Mira", sug[0]["text"])
        n = self.s.store._db.execute("SELECT COUNT(*) FROM main.claims").fetchone()[0]
        cid = self.s.accept_suggestion(sug[0]["id"])
        self.assertEqual(self.s.store._db.execute("SELECT COUNT(*) FROM main.claims"
                                                  ).fetchone()[0], n + 1)
        self.assertEqual(self.s.claim(cid)["status"], "submitted")

    def test_a_table_never_becomes_a_suggestion_or_a_fact(self):
        t = MG.replace(" ", "\t")
        ops = M.text(72, 740, CAPTION) + "".join(
            M.text(x, 700 - 14 * i, c) for i, r in enumerate(
                (("Name", "Role", "Zone"), ("Mira", "Keeper", "North"), ("Lio", "Cook", "South")))
            for x, c in zip((72, 250, 400), r))
        sid = self.pdf(self.s, one(ops))
        kinds = [k for k, _, _ in self.rows(self.s, sid)]
        self.assertEqual(kinds.count("table_row"), 3)
        self.assertEqual(self.s.suggestions(), [])
        self.assertTrue(t)

    def test_reimport_with_a_changed_pdf_marks_quotes_source_changed(self):
        path = self.write("g.pdf", "", raw=one(M.text(72, 700, MG)))
        sid = self.s.import_file(path, author_kind="named", author_name="Ada")["source_id"]
        c = self.s.learn(MG, "imported", "Lantern Garden")
        self.s.cite(c, sid, MG)
        self.s.review(c, "verified")
        self.assertEqual(self.s.respond(Q_MG).category, "established")
        self.assertEqual(self.s.reimport(sid)["state"], "unchanged")
        self.write("g.pdf", "", raw=make([Page(M.text(72, 700, MG) + M.text(72, 650, "It opens."))]))
        r = self.s.reimport(sid)
        self.assertEqual((r["state"], r["version"], r["affected_claims"]), ("new_version", 2, [c]))
        a = self.s.respond(Q_MG)
        self.assertEqual((a.category, a.reason), ("unknown", "source_changed"))
        r2 = self.s.cite(c, sid, MG)                     # cited again against version 2
        self.assertTrue(r2["supports"])
        self.assertEqual(r2["page"], 1)
        self.assertEqual(self.s.respond(Q_MG).category, "established")
        self.assertEqual([v["state"] for v in self.s.versions(sid)], ["superseded", "current"])

    def test_reimport_that_becomes_flagged_no_longer_supports(self):
        path = self.write("g.pdf", "", raw=one(M.text(72, 700, MG)))
        sid = self.s.import_file(path, author_kind="named", author_name="Ada")["source_id"]
        c = self.s.learn(MG, "imported", "Lantern Garden")
        self.s.cite(c, sid, MG)
        self.write("g.pdf", "", raw=one(q("1 1 1 rg\n", M.text(72, 700, MG)), M.text(72, 600, CALM)))
        self.s.reimport(sid)
        self.assertNotEqual(self.s.respond(Q_MG).category, "documented")
        self.assertFalse(self.s.cite(c, sid, MG)["supports"])

    def test_purged_version_text_is_gone_from_every_file(self):
        marks = ("Pdfpurgemarker", "pdfpurgemarker", "keeps secrets")
        path = self.write("g.pdf", "", raw=make([
            Page(M.text(72, 700, LIO) + M.text(72, 640, "Pdfpurgemarker keeps secrets here."))]))
        sid = self.s.import_file(path, author_kind="unknown")["source_id"]
        self.write("g.pdf", "", raw=one(M.text(72, 700, LIO)))
        self.s.reimport(sid)
        self.s.search_passages("pdfpurgemarker")
        for m in marks:
            self.assertTrue(where(self.lib.root, m), m)
        r = self.s.purge_version(sid, 1)
        self.assertEqual((r["state"], r["backups"]), ("purged", []))
        for m in marks:                                      # SK still running
            self.assertEqual(where(self.lib.root, m), set(), m)
        self.s.close()
        for m in marks:
            self.assertEqual(where(self.lib.root, m), set(), m)
        s = self.open("ana")
        self.assertEqual(s.search_passages("pdfpurgemarker"), [])
        self.assertEqual([v["state"] for v in s.versions(sid)], ["purged", "current"])
        self.assertTrue(s.search_passages("rope shed"))


class PdfSharing(PdfWorld):
    DATA = None

    def data(self):
        return make([Page(M.text(72, 700, MG) + M.text(72, 640,
                                                       "Pdfsharemarker appears in the rest.")),
                     Page(q("1 1 1 rg\n", M.text(72, 700, LIO)) + M.text(72, 600, CALM))])

    def shared_con(self):
        return sqlite3.connect(os.path.join(self.lib.root, "shared.db"))

    def shared_rows(self, table):
        con = self.shared_con()
        try:
            return con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        finally:
            con.close()

    def outbox_count(self, table):
        con = sqlite3.connect(os.path.join(self.lib.root, "outbox", self.fid("ana") + ".db"))
        try:
            return con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        finally:
            con.close()

    def share(self, levels):
        ana = self.open("ana")
        sid = self.pdf(ana, self.data(), "notes.pdf", title="Garden notes")
        c = ana.learn(MG, "imported", "Lantern Garden")
        self.assertTrue(ana.cite(c, sid, MG)["supports"])
        tok = ana.propose(c, {sid: levels})
        m = self.open("mod")
        shared = m.approve(self.fid("ana"), tok)
        return ana, m, sid, c, shared

    def test_full_text_sharing_keeps_pages_and_flags(self):
        ana, m, sid, c, shared = self.share(["quote", "full_text"])
        con = self.shared_con()
        try:
            st, vflags = con.execute("SELECT structure, flags FROM source_versions").fetchone()
            pflags = [json.loads(r[0]) for r in con.execute(
                "SELECT flags FROM passages ORDER BY ordinal")]
            facts = con.execute("SELECT COUNT(*) FROM passage_facts").fetchone()[0]
        finally:
            con.close()
        blocks = json.loads(st)
        self.assertTrue(blocks and all(len(b) == 8 for b in blocks))
        self.assertEqual(sorted({b[5] for b in blocks}), [1, 2])
        for b in blocks:
            if b[5] == 1:
                self.assertEqual(b[7], [])
            else:
                self.assertIn("pdf_hidden_text", b[7])
                self.assertEqual(len(b[6]), 4)
        self.assertIn("pdf_reference_only_pages:2", json.loads(vflags))
        self.assertEqual(len(pflags), 4)
        self.assertEqual([bool(f) for f in pflags], [False, False, True, True])
        self.assertGreater(facts, 0)                       # page 1 only
        luis = self.open("luis")
        hits = luis.search_passages("rope shed")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["page"], 2)
        self.assertFalse(hits[0]["can_support"])
        self.assertNotEqual(luis.respond(Q_LIO).category, "documented")
        h1 = luis.search_passages("lantern garden")
        self.assertEqual(h1[0]["page"], 1)
        self.assertTrue(h1[0]["can_support"])

    def test_shared_facts_exclude_flagged_pages(self):
        self.share(["quote", "full_text"])
        con = self.shared_con()
        try:
            n = con.execute("SELECT COUNT(*) FROM passage_facts f JOIN passages p ON "
                            "p.id=f.passage_id WHERE p.flags != '[]'").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(n, 0)

    def test_quote_only_sharing_of_a_flagged_quote_is_refused(self):
        for name, ops in (("white", q("1 1 1 rg\n", M.text(72, 700, MG))),
                          ("tiny", M.text(72, 700, MG, size=2)),
                          ("ocr", "q 612 0 0 792 0 0 cm /Im1 Do Q\n3 Tr\n" + M.text(72, 700, MG))):
            for levels in (["quote"], ["quote", "full_text"]):
                with self.subTest(name=name, levels=levels):
                    self.tearDown()
                    self.setUp()
                    ana = self.open("ana")
                    sid = self.pdf(ana, one(ops, M.text(72, 600, CALM)), name + ".pdf")
                    c = ana.learn(MG, "imported", "Lantern Garden")
                    self.assertFalse(ana.cite(c, sid, MG)["supports"])
                    with self.assertRaises(BrainError):
                        ana.propose(c, {sid: levels})
                    self.assertEqual(self.outbox_count("proposals"), 0)
                    self.assertEqual(self.outbox_count("material_checks"), 0)
                    self.assertNotEqual(self.open("luis").respond(Q_MG).category, "documented")

    def test_quote_only_sharing_of_a_clean_page_is_documented(self):
        ana, m, sid, c, shared = self.share(["quote"])
        self.assertEqual(self.shared_rows("passages"), 0)
        a = self.open("luis").respond(Q_MG)
        self.assertEqual((a.category, a.audience), ("documented", "shared"))

    def test_withdrawal_removes_pdf_text_from_shared_files_and_outbox(self):
        ana, m, sid, c, shared = self.share(["quote", "details", "full_text"])
        m.search_passages("pdfsharemarker")
        shared_hits = {h for mk in ("Pdfsharemarker", "pdfsharemarker")
                       for h in where(self.lib.root, mk) if not h.startswith("personal/")}
        self.assertTrue(shared_hits)
        self.assertEqual(ana.withdraw_source(sid), "complete")
        mine = {p for p in self.own_files("ana") if p.startswith("personal/")}
        markers = ("Pdfsharemarker", "pdfsharemarker", "Garden notes", "Harbour Press",
                   "Ada Guide", "Rope Shed", "harbour is calm")
        for mk in markers:                                    # sessions still open
            self.assertEqual(where(self.lib.root, mk) - mine, set(), mk)
        for s in self.sessions:
            s.close()
        self.sessions = []
        for mk in markers:
            self.assertEqual(where(self.lib.root, mk) - mine, set(), mk)
        self.assertTrue(where(self.lib.root, "Pdfsharemarker") <= mine)
        for t in ("passages", "source_versions", "passage_index", "passage_facts",
                  "evidence_locations"):
            self.assertEqual(self.shared_rows(t), 0, t)
        luis = self.open("luis")
        self.assertEqual(luis.search_passages("pdfsharemarker"), [])
        self.assertNotEqual(luis.respond(Q_MG).category, "established")


# ======================================================================== 6. privacy
class PdfPrivacy(PdfWorld):
    def test_the_file_path_is_only_in_the_owners_file(self):
        ana, luis = self.open("ana"), self.open("luis")
        path = self.write("pathmark_notes.pdf", "", raw=make([
            Page(M.text(72, 700, MG) + M.text(72, 640, "Pdfpathbody lives here."))]))
        sid = ana.import_file(path, author_kind="named", author_name="Ada")["source_id"]
        c = ana.learn(MG, "imported", "Lantern Garden")
        ana.cite(c, sid, MG)
        ana.propose(c, {sid: ["quote", "full_text"]})
        ana.close()
        luis.close()
        for m in (path, "pathmark_notes", "Pdfpathbody"):
            hits = where(self.lib.root, m)
            self.assertTrue(hits, m)
            self.assertEqual(hits - self.own_files("ana"), set(), m)
        self.assertEqual(where(self.lib.root, path) - {h for h in self.own_files("ana")
                                                       if h.startswith("personal/")}, set())

    def test_the_pdf_file_itself_and_its_metadata_are_not_kept(self):
        info = ("/Info << /Author (PDFAUTHORMARK) /Title (PDFTITLEMARK) "
                "/Producer (PDFPRODUCERMARK) /Subject (PDFSUBJECTMARK) >>")
        data = make([Page(M.text(72, 700, MG) + M.text(72, 640, "Pdfmetabody is here."))],
                    trailer_extra=info)
        for m in (b"PDFAUTHORMARK", b"PDFTITLEMARK"):
            self.assertIn(m, data)
        p = P(data)
        for m in ("PDFAUTHORMARK", "PDFTITLEMARK", "PDFPRODUCERMARK"):
            self.assertNotIn(m, p.text)
            self.assertFalse(any(m in f for f in p.flags))
        ana = self.open("ana")
        sid = self.pdf(ana, data, "meta.pdf")
        c = ana.learn(MG, "imported", "Lantern Garden")
        ana.cite(c, sid, MG)
        tok = ana.propose(c, {sid: ["quote", "details", "full_text"]})
        self.open("mod").approve(self.fid("ana"), tok)
        marks = ("PDFAUTHORMARK", "PDFTITLEMARK", "PDFPRODUCERMARK", "PDFSUBJECTMARK",
                 "%PDF-", "/Info", "FlateDecode", "/MediaBox")

        def scan():
            for m in marks:
                self.assertEqual(where(self.lib.root, m), set(), m)
            self.assertTrue(where(self.lib.root, "Pdfmetabody"))
        scan()
        for s in self.sessions:
            s.close()
        self.sessions = []
        scan()
        hits = where(self.lib.root, hashlib.sha256(data).hexdigest())
        self.assertTrue(hits <= self.own_files("ana"))      # only a hash, only the owner's


if __name__ == "__main__":
    unittest.main()
