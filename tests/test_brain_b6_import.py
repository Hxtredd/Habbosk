"""B6: importer (text / Markdown / HTML) and passages. Synthetic data only."""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import tempfile
import time
import unittest

from puzzle_assistant.brain import importer, passages
from puzzle_assistant.brain.importer import (
    MAX_BYTES, MAX_DEPTH, MAX_ELEMENTS, ImportRefused, canonical, decode, fmt_for,
    import_file, parse, read_file)
from puzzle_assistant.brain.passages import SUPPORT_KINDS, split, suspicious
from puzzle_assistant.brain.store import BrainError


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sentences(text, fmt="text"):
    p = parse(text.encode("utf-8"), fmt)
    return [p.text[x.start:x.end] for x in split(p)]


def check_invariants(tc, parsed):
    """Blocks and passages are exact, trimmed, ordered, inside bounds, sha correct."""
    text = parsed.text
    tc.assertEqual(parsed.text_sha, sha(text))
    last = 0
    for b in parsed.blocks:
        tc.assertTrue(last <= b.start < b.end <= len(text), (b, last))
        seg = text[b.start:b.end]
        tc.assertEqual(seg, seg.strip())
        last = b.end
    last_end = 0
    ordinal = 0
    for p in split(parsed):
        tc.assertTrue(last_end <= p.start < p.end <= len(text), p)
        seg = text[p.start:p.end]
        tc.assertEqual(seg, seg.strip())
        tc.assertEqual(p.sha, sha(seg))
        tc.assertEqual(p.ordinal, ordinal)
        # B6 review fix: source-level warnings (unrendered HTML) on EVERY passage
        src = tuple(f for f in passages.SOURCE_FLAGS if f in parsed.flags)
        tc.assertEqual(p.flags, suspicious(seg) + src)
        if parsed.fmt == "html":
            tc.assertIn("html_not_rendered", p.flags)
        inside = [b for b in parsed.blocks if b.start <= p.start and p.end <= b.end]
        tc.assertEqual(len(inside), 1, p)
        tc.assertNotEqual(inside[0].kind, "raw_html")
        last_end = p.end
        ordinal += 1


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="b6test")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path


# ------------------------------------------------------------------ group 1

class EncodingsAndLimits(TmpCase):
    def test_utf8(self):
        self.assertEqual(decode("héllo ✓".encode("utf-8")), "héllo ✓")

    def test_utf8_bom(self):
        p = parse(b"\xef\xbb\xbfhello", "text")
        self.assertEqual(p.text, "hello")
        self.assertIn("bom", p.flags)

    def test_utf16_le_bom(self):
        data = b"\xff\xfe" + "héllo\nwörld".encode("utf-16-le")
        p = parse(data, "text")
        self.assertEqual(p.text, "héllo\nwörld")
        self.assertIn("bom", p.flags)

    def test_utf16_be_bom(self):
        data = b"\xfe\xff" + "héllo".encode("utf-16-be")
        self.assertEqual(decode(data), "héllo")

    def test_latin1_refused(self):
        with self.assertRaises(ImportRefused):
            decode("café".encode("latin-1"))

    def test_invalid_utf8_refused(self):
        for bad in (b"\xff\xfd", b"abc\xc3", b"\x80", b"\xed\xa0\x80"):
            with self.assertRaises(ImportRefused):
                decode(bad)

    def test_bad_utf16_refused(self):
        with self.assertRaises(ImportRefused):
            decode(b"\xff\xfe\x41")          # odd length
        with self.assertRaises(ImportRefused):
            decode(b"\xff\xfe\x00\xd8")      # lone surrogate

    def test_nul_refused(self):
        with self.assertRaises(ImportRefused):
            decode(b"a\x00b")
        with self.assertRaises(ImportRefused):
            decode(b"\xff\xfe" + "a\x00b".encode("utf-16-le"))

    def test_error_base_class(self):
        self.assertTrue(issubclass(ImportRefused, BrainError))

    def test_oversize_refused_before_reading(self):
        path = os.path.join(self.dir, "big.txt")
        with open(path, "wb") as fh:
            fh.truncate(MAX_BYTES + 1)
        with self.assertRaises(ImportRefused):
            read_file(path)
        with self.assertRaises(ImportRefused):
            parse(b"a" * (MAX_BYTES + 1), "text")

    def test_exact_limit_accepted(self):
        path = os.path.join(self.dir, "ok.txt")
        with open(path, "wb") as fh:
            fh.write(b"a" * MAX_BYTES)
        self.assertEqual(len(read_file(path)), MAX_BYTES)

    def test_directory_refused(self):
        with self.assertRaises(ImportRefused):
            read_file(self.dir)

    def test_missing_refused(self):
        with self.assertRaises(ImportRefused):
            read_file(os.path.join(self.dir, "nope.txt"))

    def test_symlink_refused(self):
        target = self.write("t.txt", b"hello")
        link = os.path.join(self.dir, "l.txt")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        with self.assertRaises(ImportRefused):
            read_file(link)
        with self.assertRaises(ImportRefused):
            import_file(link)

    def test_unknown_extension_refused(self):
        path = self.write("x.dat", b"hello")
        with self.assertRaises(ImportRefused):
            fmt_for(path)
        with self.assertRaises(ImportRefused):
            import_file(path)
        self.assertEqual(import_file(path, "text").text, "hello")

    def test_extensions(self):
        for name, fmt in (("a.txt", "text"), ("a.MD", "markdown"), ("a.markdown", "markdown"),
                          ("a.html", "html"), ("a.HTM", "html")):
            self.assertEqual(fmt_for(name), fmt)

    def test_unknown_format_refused(self):
        with self.assertRaises(ImportRefused):
            parse(b"x", "pdf")

    def test_canonical(self):
        self.assertEqual(canonical("é"), "é")
        self.assertEqual(canonical("a\r\nb\rc\n"), "a\nb\nc\n")
        self.assertEqual(canonical("\r\r\n"), "\n\n")
        self.assertEqual(canonical("  a\t\r\n"), "  a\t\n")     # nothing else changes
        self.assertEqual(canonical("a​b"), "a​b")
        self.assertEqual(canonical(""), "")

    def test_hashes(self):
        data = b"\xef\xbb\xbfCaf\xc3\xa9\r\nline"
        p = parse(data, "text")
        self.assertEqual(p.bytes_sha, hashlib.sha256(data).hexdigest())
        self.assertEqual(p.text, "Café\nline")
        self.assertEqual(p.text_sha, sha("Café\nline"))

    def test_import_file_roundtrip(self):
        path = self.write("a.md", "# Hi\r\n\r\nText.\r\n".encode("utf-8"))
        p = import_file(path)
        self.assertEqual(p.fmt, "markdown")
        self.assertEqual(p.text, "# Hi\n\nText.\n")

    def test_html_flag(self):
        self.assertIn("html_not_rendered", parse(b"<p>x</p>", "html").flags)
        self.assertNotIn("html_not_rendered", parse(b"x", "text").flags)


# ------------------------------------------------------------------ group 2

class TextAndMarkdown(unittest.TestCase):
    def blocks(self, src, fmt="markdown"):
        p = parse(src.encode("utf-8"), fmt)
        check_invariants(self, p)
        return p, [(b.kind, p.text[b.start:b.end], b.heading_path, b.level)
                   for b in p.blocks]

    def test_text_paragraphs(self):
        p, bl = self.blocks("  One\n two  \n\n \t \n\nThree\n\n\n", "text")
        self.assertEqual([(k, t) for k, t, _, _ in bl],
                         [("paragraph", "One\n two"), ("paragraph", "Three")])
        self.assertEqual(bl[0][2], "")

    def test_text_empty(self):
        p, bl = self.blocks("   \n\n", "text")
        self.assertEqual(bl, [])
        self.assertEqual(split(p), [])

    def test_text_crlf(self):
        p, bl = self.blocks("a\r\nb\r\n\r\nc\r", "text")
        self.assertEqual([t for _, t, _, _ in bl], ["a\nb", "c"])

    def test_headings_and_path(self):
        src = "# A\ntext a\n## B\ntext b\n### C ###\n# D #\ntext d\n### E\n"
        _, bl = self.blocks(src)
        self.assertEqual(bl, [
            ("heading", "A", "A", 1), ("paragraph", "text a", "A", 0),
            ("heading", "B", "A > B", 2), ("paragraph", "text b", "A > B", 0),
            ("heading", "C", "A > B > C", 3), ("heading", "D", "D", 1),
            ("paragraph", "text d", "D", 0), ("heading", "E", "D > E", 3)])

    def test_heading_needs_space(self):
        _, bl = self.blocks("#nospace\n\n####### seven\n")
        self.assertEqual([k for k, _, _, _ in bl], ["paragraph", "paragraph"])

    def test_heading_replaces_deeper(self):
        _, bl = self.blocks("# A\n## B\n### C\n## D\n")
        self.assertEqual([b[2] for b in bl], ["A", "A > B", "A > B > C", "A > D"])

    def test_lists_nested_and_continuation(self):
        src = "- one\n  more of one\n  - nested\n- two\n\n1. first\n2) second\n+ plus\n"
        p, bl = self.blocks(src)
        self.assertEqual([(k, t) for k, t, _, _ in bl], [
            ("list_item", "one\n  more of one"), ("list_item", "nested"),
            ("list_item", "two"), ("list_item", "first"), ("list_item", "second"),
            ("list_item", "plus")])

    def test_deeper_nested_list_not_code(self):
        _, bl = self.blocks("- a\n    - b\n        - c\n")
        self.assertEqual([k for k, _, _, _ in bl], ["list_item"] * 3)

    def test_thematic_break_is_not_a_list(self):
        _, bl = self.blocks("text\n\n* * *\n\n---\n\nmore\n")
        self.assertEqual([t for _, t, _, _ in bl], ["text", "more"])

    def test_fenced_code(self):
        src = "intro\n\n```python\nx = 1\n\n  y = 2\n```\nafter\n\n~~~\nraw\n~~~\n"
        _, bl = self.blocks(src)
        self.assertEqual([(k, t) for k, t, _, _ in bl], [
            ("paragraph", "intro"), ("code", "x = 1\n\n  y = 2"),
            ("paragraph", "after"), ("code", "raw")])

    def test_unclosed_fence(self):
        _, bl = self.blocks("a\n\n```\ncode one\n# not a heading\n")
        self.assertEqual([(k, t) for k, t, _, _ in bl],
                         [("paragraph", "a"), ("code", "code one\n# not a heading")])

    def test_empty_fence(self):
        _, bl = self.blocks("```\n```\ntext\n")
        self.assertEqual([k for k, _, _, _ in bl], ["paragraph"])

    def test_indented_code(self):
        _, bl = self.blocks("para\n\n    code a\n\n    code b\n\nback\n")
        self.assertEqual([(k, t) for k, t, _, _ in bl], [
            ("paragraph", "para"), ("code", "code a\n\n    code b"), ("paragraph", "back")])

    def test_tab_indented_code(self):
        _, bl = self.blocks("para\n\n\tcode\n")
        self.assertEqual(bl[1][:2], ("code", "code"))

    def test_indent_after_paragraph_is_continuation(self):
        _, bl = self.blocks("para\n    still para\n")
        self.assertEqual([(k, t) for k, t, _, _ in bl], [("paragraph", "para\n    still para")])

    def test_quotes(self):
        _, bl = self.blocks("> one\n> two\n>\n> three\n\ntext\n\n  > spaced\n")
        self.assertEqual([(k, t) for k, t, _, _ in bl], [
            ("quote", "one\n> two\n>\n> three"), ("paragraph", "text"),
            ("quote", "spaced")])

    def test_tables(self):
        src = "| h1 | h2 |\n|---|:---:|\n| a | b |\n| c | d |\n\nafter\n"
        _, bl = self.blocks(src)
        self.assertEqual([(k, t) for k, t, _, _ in bl], [
            ("table_row", "h1 | h2"), ("table_row", "a | b"), ("table_row", "c | d"),
            ("paragraph", "after")])

    def test_table_without_edge_pipes(self):
        _, bl = self.blocks("a | b\n--|--\n1 | 2\n")
        self.assertEqual([t for _, t, _, _ in bl], ["a | b", "1 | 2"])

    def test_pipe_line_without_separator_is_paragraph(self):
        _, bl = self.blocks("a | b\nplain\n")
        self.assertEqual([k for k, _, _, _ in bl], ["paragraph"])

    def test_raw_html_block(self):
        src = "<div>\nsecret <b>x</b>\n</div>\n\nvisible\n"
        p, bl = self.blocks(src)
        self.assertEqual([(k, t) for k, t, _, _ in bl],
                         [("raw_html", "<div>\nsecret <b>x</b>\n</div>"), ("paragraph", "visible")])
        self.assertEqual([x.kind for x in split(p)], ["sentence"])

    def test_paragraph_interrupted_by_list_and_heading(self):
        _, bl = self.blocks("text\n- item\n# H\nmore\n")
        self.assertEqual([k for k, _, _, _ in bl],
                         ["paragraph", "list_item", "heading", "paragraph"])

    def test_number_does_not_interrupt_paragraph(self):
        _, bl = self.blocks("in the year\n1990. was good\n2. nope\n")
        self.assertEqual(len(bl), 1)

    def test_heading_path_for_all_blocks(self):
        src = "# T\n\n- a\n\n> q\n\n```\nc\n```\n\n| a |\n|---|\n| b |\n"
        _, bl = self.blocks(src)
        self.assertTrue(all(b[2] == "T" for b in bl))

    def test_markup_excluded_unicode(self):
        p, bl = self.blocks("## Ünï ✓ 😀\n\n- ça va 😀\n")
        self.assertEqual([t for _, t, _, _ in bl], ["Ünï ✓ 😀", "ça va 😀"])

    def test_crlf_markdown(self):
        p, bl = self.blocks("# A\r\n\r\n- x\r\n- y\r\n")
        self.assertEqual([t for _, t, _, _ in bl], ["A", "x", "y"])

    def test_empty_markdown(self):
        p, bl = self.blocks("")
        self.assertEqual(bl, [])


# ------------------------------------------------------------------ group 3

V = "VISIBLE"
S = "SECRET"


def _style_case(i, style):
    html = ('<p>%s one</p><div style="%s">%s <b>%s2</b><p style="display:block">%s3</p></div>'
            '<p>%s two</p>' % (V, style, S, S, S, V))
    return ("style_%02d" % i, html, [V + " one", V + " two"], [S])


STYLES = [
    "display:none", "display: none", "DISPLAY:NONE", "display : none !important",
    "color:red; display:none", "display:none;color:red", "visibility:hidden",
    "VISIBILITY: Hidden", "visibility:collapse", "font-size:0", "font-size:0px",
    "font-size: 0em", "font-size:0%", "font-size:0.0pt", "opacity:0", "opacity:0.0",
    "opacity: 0%", "opacity:0 !important", "dis/**/play:none", "display:none/*x*/",
    "d\\isplay:none", "color:transparent", "position:absolute;left:-9999px",
    "text-indent:-9999px", "width:0;height:0;overflow:hidden", "transform:scale(0)",
    "FONT-SIZE : 0.00EM ;", "display:\tnone", "opacity:.0", "color:rgba(0,0,0,0)",
]

HTML_CASES = [_style_case(i, s) for i, s in enumerate(STYLES)] + [
    ("script", "<p>VISIBLE</p><script>var x='SECRET';</script><p>VISIBLE2</p>",
     ["VISIBLE", "VISIBLE2"], ["SECRET", "var x"]),
    ("script_upper", "<P>VISIBLE</P><SCRIPT>SECRET</SCRIPT >VISIBLE2", ["VISIBLE2"], ["SECRET"]),
    ("script_fake_end", '<p>VISIBLE</p><script>var s="</p>SECRET";</script>',
     ["VISIBLE"], ["SECRET"]),
    ("script_selfclosing", "<p>VISIBLE</p><script/>SECRET", ["VISIBLE"], ["SECRET"]),
    ("script_unclosed", "<p>VISIBLE</p><script>SECRET", ["VISIBLE"], ["SECRET"]),
    ("style_tag", "<style>p{content:'SECRET'}</style><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("template", "<template><p>SECRET</p></template><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("noscript", "<noscript><p>SECRET</p></noscript><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("iframe", "<iframe src='x'>SECRET</iframe><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("object", "<object data='x'>SECRET<param name=a value=SECRET2></object>VISIBLE",
     ["VISIBLE"], ["SECRET"]),
    ("embed", "<embed src='SECRET'><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("svg", "<svg><title>SECRET</title><text>SECRET2</text></svg><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("svg_cdata", "<svg><![CDATA[SECRET]]></svg><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("math", "<math><mi>SECRET</mi></math><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("canvas", "<canvas>SECRET fallback</canvas><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("audio", "<audio controls>SECRET<source src=a></audio><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("video", "<video>SECRET<track src=a></video><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("comment", "<p>VISIBLE</p><!-- SECRET --><p>VISIBLE2</p>", ["VISIBLE2"], ["SECRET"]),
    ("comment_saved_from", "<!-- saved from url=(0022)http://SECRET.example/ --><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET", "saved from"]),
    ("comment_conditional", "<!--[if IE]>SECRET<![endif]--><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("comment_dashes", "<p>VISIBLE</p><!--! SECRET --!>after", ["VISIBLE", "after"], ["SECRET"]),
    ("comment_unclosed", "<p>VISIBLE</p><!-- SECRET", ["VISIBLE"], ["SECRET"]),
    ("comment_in_text", "<p>VIS<!-- SECRET -->IBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("cdata", "<p>VISIBLE</p><![CDATA[SECRET]]><p>VISIBLE2</p>", ["VISIBLE2"], ["SECRET"]),
    ("pi", "<?php echo 'SECRET'; ?><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("xml_pi", "<?xml version='1.0' SECRET?><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("doctype", "<!DOCTYPE html SECRET><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("hidden_attr", "<p>VISIBLE</p><p hidden>SECRET</p>", ["VISIBLE"], ["SECRET"]),
    ("hidden_attr_value", "<div hidden='hidden'>SECRET</div><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("hidden_until_found", "<div hidden=until-found>SECRET</div><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("hidden_upper", "<DIV HIDDEN>SECRET</DIV><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("aria_hidden", "<p>VISIBLE</p><span aria-hidden='true'>SECRET</span>", ["VISIBLE"], ["SECRET"]),
    ("aria_hidden_upper", "<span aria-hidden=TRUE>SECRET</span><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("aria_hidden_false", "<span aria-hidden='false'>VISIBLE</span>", ["VISIBLE"], []),
    ("hidden_container", "<div hidden><p>SECRET</p><p style='display:block'>SECRET2</p>"
     "<span aria-hidden='false'>SECRET3</span></div><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("hidden_container_deep", "<div hidden><div><div><p>SECRET</p></div>SECRET2</div></div>"
     "<p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("hidden_stray_end", "<span hidden>SECRET</div>SECRET2</span>VISIBLE", ["VISIBLE"], ["SECRET"]),
    ("hidden_li", "<ul><li hidden>SECRET</li><li>VISIBLE</li></ul>", ["VISIBLE"], ["SECRET"]),
    ("hidden_td", "<table><tr><td>VISIBLE<td hidden>SECRET</table>", ["VISIBLE"], ["SECRET"]),
    ("hidden_heading", "<h1 hidden>SECRET</h1><h2>VISIBLE</h2>", ["VISIBLE"], ["SECRET"]),
    ("input_hidden", "<form><input type='hidden' name=a value=SECRET><p>VISIBLE</p></form>",
     ["VISIBLE"], ["SECRET"]),
    ("input_hidden_upper", "<input TYPE=HIDDEN value=SECRET><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("input_text", "<input value='SECRET' placeholder='SECRET2'><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("textarea", "<textarea>SECRET</textarea><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("textarea_trick", "<div><textarea></div>SECRET</textarea>VISIBLE", ["VISIBLE"], ["SECRET"]),
    ("select_option", "<select><option>SECRET<option>SECRET2</select><p>VISIBLE</p>",
     ["VISIBLE"], ["SECRET"]),
    ("button", "<button>SECRET</button><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("attr_alt", "<img alt='SECRET' src='a.png'><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("attr_title", "<p title='SECRET'>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("attr_href", "<a href='http://SECRET.example/x'>VISIBLE</a>", ["VISIBLE"], ["SECRET", "http"]),
    ("attr_data", "<div data-x='SECRET' data-y=SECRET2>VISIBLE</div>", ["VISIBLE"], ["SECRET"]),
    ("attr_aria_label", "<p aria-label='SECRET' class='SECRET2'>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("attr_malformed", "<p class=a\"b title=SECRET/ >VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("attr_unterminated_quote", "<p>VISIBLE</p><div class='a' title=\"SECRET", ["VISIBLE"], ["SECRET"]),
    ("attr_dup_style", "<div style='color:red' style='display:none'>SECRET</div>VISIBLE",
     ["VISIBLE"], ["SECRET"]),
    ("head_title_meta", "<html><head><title>SECRET</title><meta name=description content=SECRET2>"
     "<link href=SECRET3></head><body><p>VISIBLE</p></body></html>", ["VISIBLE"], ["SECRET"]),
    ("head_unclosed", "<html><head><title>SECRET</title><body><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("head_text", "<head>SECRET<title>SECRET2</title></head><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("title_in_body", "<p>VISIBLE</p><title>SECRET</title>", ["VISIBLE"], ["SECRET"]),
    ("meta_in_body", "<p>VISIBLE</p><meta content='SECRET'>", ["VISIBLE"], ["SECRET"]),
    ("entity_literal", "<p>VISIBLE &lt;script&gt;alert(1)&lt;/script&gt; text</p>",
     ["VISIBLE <script>alert(1)</script> text"], []),
    ("entity_in_script", "<script>&lt;p&gt;SECRET</script><p>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("entity_numeric", "<p>VIS&#73;BLE &amp; &#x41;</p>", ["VISIBLE & A"], []),
    ("entity_zero_width", "<p>VIS&#x200B;IBLE here.</p>", ["VIS​IBLE"], []),
    ("entity_bidi", "<p>VISIBLE &#x202E;txet</p>", ["‮"], []),
    ("entity_nul", "<p>VISIBLE &#0; x &#x0; y</p>", ["VISIBLE"], ["\x00"]),
    ("entity_surrogate", "<p>VISIBLE &#xD800; x</p>", ["VISIBLE"], ["\ud800"]),
    ("homoglyph", "<p>Visit p&#x430;ypal now.</p>", ["Visit"], []),
    ("unclosed_p", "<p>VISIBLE one<p>VISIBLE two<div>VISIBLE three", ["VISIBLE three"], []),
    ("misnested", "<b><i>VISIBLE</b>text</i> <u>more", ["VISIBLEtext"], []),
    ("unclosed_hidden_swallows_rest", "<p>VISIBLE</p><div hidden><p>SECRET</p>", ["VISIBLE"], ["SECRET"]),
    ("stray_end_tags", "</div></p></span><p>VISIBLE</p></b></table>", ["VISIBLE"], []),
    ("unclosed_tag_at_eof", "<p>VISIBLE</p><img alt='SECRET", ["VISIBLE"], ["SECRET"]),
    ("unclosed_tag_at_eof2", "<p>VISIBLE</p><div hidden SECRET", ["VISIBLE"], ["SECRET"]),
    ("after_body", "<html><body><p>VISIBLE</p></body></html>trailing text VISIBLE2",
     ["VISIBLE2"], []),
    ("after_html", "<p>VISIBLE</p></html><p>VISIBLE2</p>", ["VISIBLE2"], []),
    ("uppercase_tags", "<DIV><H1>VISIBLE</H1><P>VISIBLE2</P><SCRIPT>SECRET</SCRIPT></DIV>",
     ["VISIBLE", "VISIBLE2"], ["SECRET"]),
    ("nested_hidden_inside_visible", "<div><span>VISIBLE<span style='display:none'>SECRET"
     "<span>SECRET2</span></span> tail</span></div>", ["VISIBLE", "tail"], ["SECRET"]),
    ("hidden_with_visible_child_attrs", "<div hidden><span style='display:inline' "
     "aria-hidden='false'>SECRET</span></div>VISIBLE", ["VISIBLE"], ["SECRET"]),
    ("mixed_comment_text", "<p>A <!--x--> B <!-- SECRET --> C</p>", ["A", "B", "C"], ["SECRET"]),
    ("nul_free_big_attr", "<p title='" + "x" * 100000 + "SECRET'>VISIBLE</p>", ["VISIBLE"], ["SECRET"]),
    ("br", "<p>VISIBLE<br>two<br/>three</p>", ["VISIBLE two three"], []),
    ("pre_spaces", "<pre>  VISIBLE\n   two  \n</pre>", ["VISIBLE\n   two"], []),
    ("table_cells", "<table><tr><th>VISIBLE</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>",
     ["VISIBLE | b", "1 | 2"], []),
    ("nested_lists", "<ul><li>VISIBLE<ul><li>inner</li></ul>tail</li><li>next</li></ul>",
     ["VISIBLE", "inner", "tail", "next"], []),
    ("blockquote", "<blockquote><p>VISIBLE quote.</p></blockquote>", ["VISIBLE quote."], []),
    ("figure_etc", "<figure><figcaption>VISIBLE cap</figcaption></figure><address>Addr</address>",
     ["VISIBLE cap", "Addr"], []),
]


class MaliciousHtml(unittest.TestCase):
    def test_corpus_size(self):
        self.assertGreaterEqual(len(HTML_CASES), 60)
        self.assertEqual(len({c[0] for c in HTML_CASES}), len(HTML_CASES))

    def test_corpus(self):
        for name, html, must, must_not in HTML_CASES:
            with self.subTest(name):
                p = parse(html.encode("utf-8"), "html")
                check_invariants(self, p)
                pas = [p.text[x.start:x.end] for x in split(p)]
                self.assertIn("html_not_rendered", p.flags)
                self.assertNotIn("\x00", p.text)
                for m in must:
                    self.assertIn(m, p.text)
                for m in must_not:
                    self.assertNotIn(m, p.text)
                    for t in pas:
                        self.assertNotIn(m, t)

    def test_block_kinds(self):
        p = parse(b"<h1>T</h1><p>Para.</p><ul><li>item</li></ul><pre>c  d</pre>"
                  b"<blockquote><p>Q one. Q two.</p></blockquote>"
                  b"<table><tr><td>a</td><td>b</td></tr></table>", "html")
        self.assertEqual([(b.kind, p.text[b.start:b.end], b.heading_path) for b in p.blocks], [
            ("heading", "T", "T"), ("paragraph", "Para.", "T"), ("list_item", "item", "T"),
            ("code", "c  d", "T"), ("quote", "Q one. Q two.", "T"), ("table_row", "a | b", "T")])
        kinds = [x.kind for x in split(p)]
        self.assertEqual(kinds, ["heading", "sentence", "list_item", "code", "quote_sentence",
                                 "quote_sentence", "table_row"])

    def test_heading_paths(self):
        p = parse(b"<h1>A</h1><p>a</p><h2>B</h2><p>b</p><h2>C</h2><p>c</p><h1>D</h1><p>d</p>",
                  "html")
        self.assertEqual([b.heading_path for b in p.blocks],
                         ["A", "A", "A > B", "A > B", "A > C", "A > C", "D", "D"])
        self.assertEqual([b.level for b in p.blocks if b.kind == "heading"], [1, 2, 2, 1])

    def test_loose_body_text(self):
        p = parse(b"<body>loose text<p>para</p>more loose</body>", "html")
        self.assertEqual([p.text[b.start:b.end] for b in p.blocks],
                         ["loose text", "para", "more loose"])

    def test_whitespace_collapse(self):
        p = parse(b"<p>  a \n\t b   <b>c</b>  d </p>", "html")
        self.assertEqual(p.text, "a b c d")

    def test_text_is_built_with_blank_lines(self):
        p = parse(b"<p>one</p><p>two</p>", "html")
        self.assertEqual(p.text, "one\n\ntwo")
        self.assertEqual([(b.start, b.end) for b in p.blocks], [(0, 3), (5, 8)])

    def test_nfc_in_html(self):
        p = parse("<p>café</p>".encode("utf-8"), "html")
        self.assertEqual(p.text, "café")
        check_invariants(self, p)

    def test_depth_limit(self):
        with self.assertRaises(ImportRefused):
            parse(b"<div>" * (MAX_DEPTH + 1), "html")
        ok = parse(b"<div>" * MAX_DEPTH + b"x", "html")
        self.assertEqual(ok.text, "x")

    def test_depth_limit_inline_and_hidden(self):
        with self.assertRaises(ImportRefused):
            parse(b"<span>" * 400 + b"x", "html")
        with self.assertRaises(ImportRefused):
            parse(b"<div hidden>" + b"<b>" * 400, "html")

    def test_element_limit(self):
        with self.assertRaises(ImportRefused):
            parse(b"<b></b>" * (MAX_ELEMENTS + 1), "html")
        with self.assertRaises(ImportRefused):
            parse(b"<br>" * (MAX_ELEMENTS + 1), "html")
        ok = parse(b"<b></b>" * MAX_ELEMENTS, "html")
        self.assertEqual(ok.text, "")

    def test_huge_text_node(self):
        word = "word. " * 400000
        p = parse(("<p>" + word + "</p>").encode("utf-8"), "html")
        self.assertEqual(p.text, word.strip().replace("  ", " "))
        self.assertEqual(len(p.blocks), 1)
        check_invariants(self, p)

    def test_hidden_markers_never_in_flags_or_hash(self):
        p = parse(b"<p>a</p><p hidden>SECRET</p>", "html")
        self.assertEqual(p.text, "a")
        self.assertEqual(p.text_sha, sha("a"))

    def test_zero_width_entity_flagged(self):
        p = parse(b"<p>Hello&#x200B; world. Plain one.</p>", "html")
        pas = split(p)
        self.assertEqual(pas[0].flags, ("suspicious", "html_not_rendered"))
        self.assertEqual(pas[1].flags, ("html_not_rendered",))

    def test_bidi_entity_flagged(self):
        p = parse(b"<p>abc &#x202E;def.</p>", "html")
        self.assertEqual(split(p)[0].flags, ("suspicious", "html_not_rendered"))

    def test_homoglyph_flagged(self):
        p = parse("<p>p&#x430;ypal is a site.</p>".encode("utf-8"), "html")
        self.assertEqual(split(p)[0].flags, ("mixed_script", "html_not_rendered"))

    def test_entity_nul_replaced(self):
        p = parse(b"<p>a&#0;b</p>", "html")
        self.assertNotIn("\x00", p.text)
        self.assertEqual(len(p.text), 3)

    def test_script_text_never_blocks(self):
        p = parse(b"<script>a</script><style>b</style><p>c</p>", "html")
        self.assertEqual(len(p.blocks), 1)

    def test_literal_script_entity_is_only_text(self):
        p = parse(b"<p>&lt;script&gt;SECRET&lt;/script&gt;</p>", "html")
        self.assertEqual(p.text, "<script>SECRET</script>")

    def test_wild_inputs_never_crash(self):
        rnd = random.Random(7)
        bits = ["<", ">", "</", "/>", "<p>", "</p>", "<div hidden>", "</div>", "<!--", "-->",
                "<![CDATA[", "]]>", "<script>", "</script>", "&", "&#", "&#x", ";", '"', "'",
                "=", "a", " ", "\n", "<td>", "<tr>", "<li>", "<pre>", "<h1>", "text", "<!",
                "<?", "?>", "<b>", "</b>", "<svg>", "</svg>", "style='display:none'"]
        for _ in range(400):
            html = "".join(rnd.choice(bits) for _ in range(rnd.randint(1, 40)))
            try:
                p = parse(html.encode("utf-8"), "html")
            except ImportRefused:
                continue
            check_invariants(self, p)


# ------------------------------------------------------------------ group 4

class Passages(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(sentences("Hello world. This is it! Really? Yes."),
                         ["Hello world.", "This is it!", "Really?", "Yes."])

    def test_abbreviations_en(self):
        self.assertEqual(sentences("Mr. Smith met Dr. Jones. They talked."),
                         ["Mr. Smith met Dr. Jones.", "They talked."])
        self.assertEqual(sentences("See fig. 3 and e.g. Prof. Lee. Done."),
                         ["See fig. 3 and e.g. Prof. Lee.", "Done."])
        self.assertEqual(sentences("Apples, etc. The end."), ["Apples, etc. The end."])
        self.assertEqual(sentences("It cost approx. Five dollars. Fine."),
                         ["It cost approx. Five dollars.", "Fine."])
        self.assertEqual(sentences("Mrs. Brown, Ms. Green and St. Paul. Ok."),
                         ["Mrs. Brown, Ms. Green and St. Paul.", "Ok."])
        self.assertEqual(sentences("Team vs. Rivals. Done."), ["Team vs. Rivals.", "Done."])

    def test_abbreviations_es(self):
        self.assertEqual(sentences("El Dr. García llegó. Sra. López no vino."),
                         ["El Dr. García llegó.", "Sra. López no vino."])
        self.assertEqual(sentences("La Dra. Ruiz y Dña. Ana. Srta. Paz también."),
                         ["La Dra. Ruiz y Dña. Ana.", "Srta. Paz también."])
        self.assertEqual(sentences("Aprox. Diez cosas, p.ej. Muchas. Fin."),
                         ["Aprox. Diez cosas, p.ej. Muchas.", "Fin."])
        self.assertEqual(sentences("El nº. Cinco salió. Bien."),
                         ["El nº. Cinco salió.", "Bien."])

    def test_initials(self):
        self.assertEqual(sentences("J. Smith wrote it. J. R. R. Tolkien too."),
                         ["J. Smith wrote it.", "J. R. R. Tolkien too."])

    def test_decimals_and_versions(self):
        self.assertEqual(sentences("Pi is 3.5 or so. Next one 2.0.1 here."),
                         ["Pi is 3.5 or so.", "Next one 2.0.1 here."])

    def test_ellipsis(self):
        self.assertEqual(sentences("Wait... Then he left… Later it rained."),
                         ["Wait...", "Then he left…", "Later it rained."])
        self.assertEqual(sentences("Wait... then he left."), ["Wait... then he left."])

    def test_lowercase_does_not_split(self):
        self.assertEqual(sentences("It is v1. and so on."), ["It is v1. and so on."])
        self.assertEqual(sentences("What? yes."), ["What? yes."])

    def test_quotes_and_brackets_after_period(self):
        self.assertEqual(sentences('He said "Stop." Then left.'),
                         ['He said "Stop."', "Then left."])
        self.assertEqual(sentences("He said “Stop.” Then left."),
                         ["He said “Stop.”", "Then left."])
        self.assertEqual(sentences("It ended (really.) Next."), ["It ended (really.)", "Next."])
        self.assertEqual(sentences("Say ‘no.’» Next."), ["Say ‘no.’»", "Next."])

    def test_opening_quote_starts_sentence(self):
        self.assertEqual(sentences('It rained. "Why?" she asked.'),
                         ["It rained.", '"Why?" she asked.'])

    def test_spanish_marks(self):
        self.assertEqual(sentences("¿Qué hora es? ¿Las tres? ¡Sí! Claro."),
                         ["¿Qué hora es?", "¿Las tres?", "¡Sí!", "Claro."])

    def test_digit_start(self):
        self.assertEqual(sentences("It ended. 2024 began."), ["It ended.", "2024 began."])

    def test_whitespace_runs_and_newlines(self):
        p = parse("One.   Two.\nThree.\t Four.".encode(), "text")
        self.assertEqual([p.text[x.start:x.end] for x in split(p)],
                         ["One.", "Two.", "Three.", "Four."])
        self.assertEqual(sentences("A sentence\nover lines. Next."),
                         ["A sentence\nover lines.", "Next."])

    def test_no_terminator(self):
        self.assertEqual(sentences("no end here"), ["no end here"])
        self.assertEqual(sentences("ends with dots..."), ["ends with dots..."])

    def test_list_item_one_passage(self):
        p = parse(b"- One. Two. Three.\n- Four.\n", "markdown")
        pas = split(p)
        self.assertEqual([(x.kind, p.text[x.start:x.end]) for x in pas],
                         [("list_item", "One. Two. Three."), ("list_item", "Four.")])

    def test_kinds_per_block(self):
        src = ("# Head\n\nPara one. Para two.\n\n- item\n\n> Quote one. Quote two.\n\n"
               "```\ncode. here.\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<div>raw. html.</div>\n")
        p = parse(src.encode(), "markdown")
        got = [(x.kind, p.text[x.start:x.end]) for x in split(p)]
        self.assertEqual(got, [
            ("heading", "Head"), ("sentence", "Para one."), ("sentence", "Para two."),
            ("list_item", "item"), ("quote_sentence", "Quote one."),
            ("quote_sentence", "Quote two."), ("code", "code. here."),
            ("table_row", "a | b"), ("table_row", "1 | 2")])

    def test_support_kinds(self):
        self.assertEqual(SUPPORT_KINDS, ("sentence", "list_item"))

    def test_heading_path_and_ordinal(self):
        p = parse(b"# A\n\nOne. Two.\n\n## B\n\nThree.\n", "markdown")
        pas = split(p)
        self.assertEqual([x.ordinal for x in pas], list(range(len(pas))))
        self.assertEqual([x.heading_path for x in pas], ["A", "A", "A", "A > B", "A > B"])

    def test_sha(self):
        p = parse("Café. Ünï.".encode(), "text")
        for x in split(p):
            self.assertEqual(x.sha, sha(p.text[x.start:x.end]))

    def test_suspicious_flags(self):
        self.assertEqual(suspicious("plain text"), ())
        self.assertEqual(suspicious("tab\tnewline\nok"), ())
        for ch in ("​", "‌", "‍", "‎", "‏", "‪", "‮",
                   "⁦", "⁩", "⁠", "⁤", "﻿", "­", "",
                   "\x07", "\x1b", "\x7f", "\x85", "\r", "\u0000"):
            self.assertEqual(suspicious("a" + ch + "b"), ("suspicious",), repr(ch))
        self.assertEqual(suspicious("naïve café 😀 — ok"), ())

    def test_mixed_script(self):
        self.assertEqual(suspicious("pаypal"), ("mixed_script",))     # Cyrillic a
        self.assertEqual(suspicious("αlpha"), ("mixed_script",))      # Greek alpha
        self.assertEqual(suspicious("Привет world"), ())                   # separate words
        self.assertEqual(suspicious("Привет"), ())
        self.assertEqual(suspicious("αβγ and abc"), ())
        self.assertEqual(suspicious("pаypal ​"), ("suspicious", "mixed_script"))
        self.assertEqual(suspicious("a1-а"), ())                      # not one word

    def test_flags_on_passages(self):
        p = parse("Clean one. Zero​width two. Pаypal three.".encode("utf-8"), "text")
        self.assertEqual([x.flags for x in split(p)],
                         [(), ("suspicious",), ("mixed_script",)])

    def test_no_empty_passages(self):
        p = parse(b"...\n\n!!!\n\n. . .\n", "text")
        for x in split(p):
            self.assertLess(x.start, x.end)


# ------------------------------------------------------------------ group 5

WORDS = ["alpha", "beta", "gamma", "Delta", "café", "naïve", "Zoë", "😀", "ñandú", "x1",
         "3.5", "Mr.", "Dr.", "J.", "etc.", "Hello.", "Yes!", "Why?", "end.", "…", "¿Qué?",
         "¡Sí!", "(ok.)", '"quote."', "a|b", "#", "-", "*", ">", "1.", "<b>", "&amp;",
         "é", "ÅÅ", "​", "pаy"]
PUNCT_SEPS = [" ", " ", " ", "  ", "\t", " "]


def rnd_words(rnd, lo=1, hi=14):
    return "".join(rnd.choice(WORDS) + rnd.choice(PUNCT_SEPS)
                   for _ in range(rnd.randint(lo, hi))).strip() or "w"


def gen_text(rnd):
    paras = []
    for _ in range(rnd.randint(0, 6)):
        lines = [rnd_words(rnd) for _ in range(rnd.randint(1, 3))]
        indent = rnd.choice(["", "", "  ", "\t"])
        paras.append(indent + ("\n" + indent).join(lines))
    sep = rnd.choice(["\n\n", "\n \n", "\n\n\n", "\n\t\n"])
    return sep.join(paras) + rnd.choice(["", "\n", "\n\n"])


def gen_markdown(rnd):
    out = []
    for _ in range(rnd.randint(0, 14)):
        k = rnd.choice(["h", "p", "ul", "ol", "nest", "code", "icode", "quote", "table",
                        "html", "hr", "blank", "cont", "fence_open"])
        if k == "h":
            out.append("#" * rnd.randint(1, 6) + " " + rnd_words(rnd) + rnd.choice(["", " ##", " "]))
        elif k == "p":
            out.append("\n".join(rnd_words(rnd) for _ in range(rnd.randint(1, 3))))
        elif k == "ul":
            out.extend(rnd.choice(["- ", "* ", "+ "]) + rnd_words(rnd) for _ in range(rnd.randint(1, 3)))
        elif k == "ol":
            out.extend("%d%s " % (i + 1, rnd.choice([".", ")"])) + rnd_words(rnd)
                       for i in range(rnd.randint(1, 3)))
        elif k == "nest":
            out.append("- " + rnd_words(rnd))
            out.append("  " * rnd.randint(1, 3) + "- " + rnd_words(rnd))
            out.append("  " + rnd_words(rnd))
        elif k == "cont":
            out.append("- " + rnd_words(rnd))
            out.append("   " + rnd_words(rnd))
        elif k == "code":
            f = rnd.choice(["```", "~~~", "````"])
            out.append(f + rnd.choice(["", "py"]))
            out.extend(rnd_words(rnd) for _ in range(rnd.randint(0, 3)))
            out.append(f)
        elif k == "fence_open":
            out.append("```")
            out.extend(rnd_words(rnd) for _ in range(rnd.randint(0, 3)))
        elif k == "icode":
            out.append("")
            out.extend(rnd.choice(["    ", "\t"]) + rnd_words(rnd) for _ in range(rnd.randint(1, 3)))
        elif k == "quote":
            out.extend(rnd.choice(["> ", ">", "  > "]) + rnd_words(rnd) for _ in range(rnd.randint(1, 3)))
        elif k == "table":
            out.append("| a | b |")
            out.append(rnd.choice(["|---|---|", "|:--|--:|", "--|--"]))
            out.extend("| " + rnd_words(rnd, 1, 3) + " | " + rnd_words(rnd, 1, 3) + " |"
                       for _ in range(rnd.randint(0, 3)))
        elif k == "html":
            out.append("<div>" + rnd_words(rnd) + "</div>")
            out.append(rnd_words(rnd))
        elif k == "hr":
            out.append(rnd.choice(["---", "* * *", "___"]))
        else:
            out.append(rnd.choice(["", " ", "\t", "   "]))
        if rnd.random() < 0.6:
            out.append("")
    nl = rnd.choice(["\n", "\n", "\r\n", "\r"])
    return nl.join(out) + rnd.choice(["", "\n"])


def gen_html(rnd, depth=0):
    parts = []
    for _ in range(rnd.randint(0, 5)):
        k = rnd.choice(["p", "h", "ul", "table", "pre", "bq", "div", "text", "hidden",
                        "script", "comment", "br", "inline", "unclosed"])
        if k == "p":
            parts.append("<p>" + rnd_words_html(rnd) + rnd.choice(["</p>", ""]))
        elif k == "h":
            n = rnd.randint(1, 6)
            parts.append("<h%d>%s</h%d>" % (n, rnd_words_html(rnd), n))
        elif k == "ul":
            parts.append("<ul>" + "".join("<li>" + rnd_words_html(rnd)
                                          + (gen_html(rnd, depth + 1) if depth < 2 and rnd.random() < .3 else "")
                                          + rnd.choice(["</li>", ""]) for _ in range(rnd.randint(1, 3)))
                         + "</ul>")
        elif k == "table":
            parts.append("<table>" + "".join(
                "<tr>" + "".join("<td>" + rnd_words_html(rnd, 1, 3) + rnd.choice(["</td>", ""])
                                 for _ in range(rnd.randint(1, 3))) + rnd.choice(["</tr>", ""])
                for _ in range(rnd.randint(1, 3))) + "</table>")
        elif k == "pre":
            parts.append("<pre>  " + rnd_words_html(rnd) + "\n   " + rnd_words_html(rnd) + "</pre>")
        elif k == "bq":
            parts.append("<blockquote>" + (gen_html(rnd, depth + 1) if depth < 2 else "") + "</blockquote>")
        elif k == "div":
            parts.append("<div>" + (gen_html(rnd, depth + 1) if depth < 3 else "x") + "</div>")
        elif k == "text":
            parts.append(rnd_words_html(rnd))
        elif k == "hidden":
            parts.append(rnd.choice([
                "<div hidden>HIDDENMARK %s</div>", "<p style='display:none'>HIDDENMARK %s</p>",
                "<span aria-hidden='true'>HIDDENMARK %s</span>",
                "<div style='font-size:0'><p>HIDDENMARK %s</p></div>"]) % rnd_words_html(rnd))
        elif k == "script":
            parts.append("<script>HIDDENMARK %s</script>" % rnd.choice(["a<b", "</p>", "x"]))
        elif k == "comment":
            parts.append("<!-- HIDDENMARK -->")
        elif k == "br":
            parts.append("a<br>b")
        elif k == "inline":
            parts.append("<b>" + rnd_words_html(rnd) + "</b><i title='HIDDENMARK'>x</i>")
        else:
            parts.append("<span>" + rnd_words_html(rnd))
    return "".join(parts)


def rnd_words_html(rnd, lo=1, hi=8):
    return rnd_words(rnd, lo, hi).replace("<", "&lt;")


class RoundTrip(unittest.TestCase):
    N_PER_FORMAT = 750

    def run_format(self, gen, fmt, seed):
        rnd = random.Random(seed)
        for i in range(self.N_PER_FORMAT):
            src = gen(rnd)
            data = src.encode("utf-8")
            if rnd.random() < 0.1:
                data = b"\xef\xbb\xbf" + data
            try:
                a = parse(data, fmt)
            except ImportRefused:
                self.assertEqual(fmt, "html", src)
                continue
            b = parse(data, fmt)
            self.assertEqual(a, b)
            self.assertEqual(split(a), split(b))
            check_invariants(self, a)
            if fmt == "html":
                self.assertNotIn("HIDDENMARK", a.text, src)
            self.assertEqual(a.text, canonical(a.text))

    def test_text(self):
        self.run_format(gen_text, "text", 1001)

    def test_markdown(self):
        self.run_format(gen_markdown, "markdown", 2002)

    def test_html(self):
        self.run_format(gen_html, "html", 3003)

    def test_total_documents(self):
        self.assertGreaterEqual(3 * self.N_PER_FORMAT, 2000)


# ------------------------------------------------------------------ group 6

class LinearTime(unittest.TestCase):
    LIMIT = 20.0

    def timed(self, data, fmt):
        t0 = time.time()
        p = parse(data, fmt)
        pas = split(p)
        elapsed = time.time() - t0
        self.assertLess(elapsed, self.LIMIT)
        return p, pas

    def test_two_million_dots(self):
        p, pas = self.timed(b"." * 2_000_000, "text")
        self.assertEqual(len(p.text), 2_000_000)
        self.assertLessEqual(len(pas), 2)

    def test_dot_space_runs_text(self):
        p, pas = self.timed(b"Ab. Cd. " * 500_000, "text")
        self.assertGreater(len(pas), 100)
        self.timed(b"a. " * 1_000_000, "text")

    def test_dot_space_runs_markdown(self):
        self.timed(b"a. " * 1_200_000, "markdown")

    def test_dot_space_runs_html(self):
        self.timed(b"<p>" + b"a. " * 1_000_000 + b"</p>", "html")

    def test_closers_and_spaces(self):
        self.timed(b'a.") ' * 600_000, "text")
        self.timed(b"a." + b" " * 3_000_000 + b"B", "text")
        self.timed(b". ) . ) " * 500_000, "text")

    def test_many_lines(self):
        self.timed(b"- a\n" * 1_000_000, "markdown")
        self.timed(b"\n" * 3_000_000, "markdown")
        self.timed(b"> a\n" * 1_000_000, "markdown")

    def test_long_single_line_markdown(self):
        self.timed(b"#" * 2_000_000, "markdown")
        self.timed(b"|" * 2_000_000 + b"\n|-|\n", "markdown")
        self.timed(b"`" * 2_000_000, "markdown")

    def test_html_many_tags(self):
        self.timed(b"<p>x</p>" * 150_000, "html")
        self.timed(b"</div>" * 500_000, "html")


if __name__ == "__main__":
    unittest.main()
