"""Windows binary mode for SK Brain's low-level file access (Windows check of f688383).

On Windows ``os.open`` opens a file in TEXT mode unless O_BINARY is given:
reading turns every CR LF into LF and stops at the first 0x1A byte, writing
turns every LF into CR LF. importer.read_file had no O_BINARY, so on Windows
PDFs whose bytes happened to hold CR LF or 0x1A were refused as "damaged"
(PdfReadError), and a 0x1A cut TXT / MD / HTML files short; the key store's
write had none either, so a protected key holding an LF byte could never be
loaded again.

Every test here runs on every system. On Windows they use the real text mode
of the C runtime; elsewhere ``text_mode_os`` emulates exactly what it does to
a file descriptor opened without O_BINARY (only inside the module under test,
never for tempfile, sqlite or the test itself). The last class runs the same
checks against copies of importer.py / keystore.py with O_BINARY removed and
requires them to FAIL - the deliberate breaks.
"""
import ast
import contextlib
import hashlib
import os
import random
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import b6_pdf_maker as M  # noqa: E402
from puzzle_assistant.brain import importer, keystore  # noqa: E402
from puzzle_assistant.brain.importer import ImportRefused  # noqa: E402
from tests.test_brain_b6 import World  # noqa: E402

BRAIN = os.path.join(ROOT, "puzzle_assistant", "brain")
WINDOWS = sys.platform == "win32"
MG = "Mira maintains the Lantern Garden."
CALM = "The harbour is calm today."


# ----------------------------------------------------------------- Windows text mode
class _TextModeOs:
    """``os`` as seen by one module, with the C runtime's text mode emulated for
    every descriptor that module opens without O_BINARY."""
    FAKE_BINARY = 0x40000000                   # not a Linux / macOS open flag

    def __init__(self):
        self.O_BINARY = self.FAKE_BINARY
        self.text_fds = {}                     # fd -> pending translated read data
        self.text_written = set()              # paths written in text mode
        self.opened = []                       # (path, binary?)

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags, mode=0o777, *a, **k):
        binary = bool(flags & self.FAKE_BINARY)
        fd = os.open(path, flags & ~self.FAKE_BINARY, mode, *a, **k)
        self.opened.append((path, binary))
        if not binary:
            self.text_fds[fd] = None
            if flags & (os.O_WRONLY | os.O_RDWR):
                self.text_written.add(os.path.abspath(path))
        return fd

    def read(self, fd, n):
        if fd not in self.text_fds:
            return os.read(fd, n)
        if self.text_fds[fd] is None:          # read it all once, as text mode sees it
            raw = bytearray()
            while True:
                c = os.read(fd, 1 << 20)
                if not c:
                    break
                raw += c
            cut = raw.find(b"\x1a")            # Ctrl-Z ends a text-mode read
            self.text_fds[fd] = bytes(raw if cut < 0 else raw[:cut]).replace(b"\r\n", b"\n")
        out, self.text_fds[fd] = self.text_fds[fd][:n], self.text_fds[fd][n:]
        return out

    def close(self, fd):
        self.text_fds.pop(fd, None)
        return os.close(fd)

    def replace(self, src, dst, *a, **k):
        # bytes written through a text-mode descriptor: every LF became CR LF
        if os.path.abspath(src) in self.text_written:
            self.text_written.discard(os.path.abspath(src))
            with open(src, "rb") as fh:
                data = fh.read()
            with open(src, "wb") as fh:
                fh.write(data.replace(b"\n", b"\r\n"))
        return os.replace(src, dst, *a, **k)


@contextlib.contextmanager
def text_mode_os(*modules):
    """Windows: the real C runtime (nothing to emulate). Elsewhere: each
    module's ``os`` behaves like Windows' for descriptors opened without O_BINARY."""
    if WINDOWS:
        yield None
        return
    fake = _TextModeOs()
    with contextlib.ExitStack() as st:
        for m in modules:
            st.enter_context(mock.patch.object(m, "os", fake))
        yield fake


def load_copy(name, edit):
    """A copy of puzzle_assistant/brain/<name>.py with `edit` applied to its
    source, loaded as a module of the same package (for deliberate breaks)."""
    with open(os.path.join(BRAIN, name + ".py"), encoding="utf-8") as fh:
        src = fh.read()
    new = edit(src)
    assert new != src, "the deliberate break did not change " + name
    full = "puzzle_assistant.brain._broken_" + name
    mod = types.ModuleType(full)
    mod.__package__ = "puzzle_assistant.brain"
    sys.modules[full] = mod                    # dataclasses look their module up
    try:
        exec(compile(new, os.path.join(BRAIN, name + ".py"), "exec"), mod.__dict__)
    finally:
        del sys.modules[full]
    return mod, new


def without_binary(src):
    return src.replace('getattr(os, "O_BINARY", 0) | ', "").replace(
        ' | getattr(os, "O_BINARY", 0)', "")


# ----------------------------------------------------------------- awkward files
def pdf_with_crlf_and_ctrl_z():
    """Two pages; raw CR LF and 0x1A inside an uncompressed content stream
    (PDF comments, so they are bytes the reader must keep but never shows)."""
    ops = "% a CR LF here\r\n% and a \x1a byte here\r\n" + M.text(72, 700, MG)
    data = M.make([M.Page(ops), M.Page(M.text(72, 700, CALM))], compress=False)
    assert b"\r\n" in data and b"\x1a" in data
    return data


def compressed_pdf_with_crlf_and_ctrl_z():
    """A normal compressed PDF whose compressed bytes happen to hold both (the
    real case on the PC: such PDFs were refused as damaged). Searched for, as
    zlib versions (zlib-ng on Windows' Python 3.14) compress differently."""
    for k in range(5000):
        r = random.Random(k)
        lines = ["Note " + " ".join("".join(r.choice("abcdefghijklmnopqrstuvwxyz")
                                            for _ in range(r.randint(2, 9)))
                                    for _ in range(8)) + "." for _ in range(20)]
        data = M.make([M.Page(M.text(72, 700, MG) + "".join(
            M.text(72, 650 - 14 * i, ln) for i, ln in enumerate(lines)))])
        if b"\r\n" in data and b"\x1a" in data:
            return data, k
    raise AssertionError("no such PDF found")


TEXT = "Line one.\r\nAfter \x1a the byte, the text goes on.\r\nMira maintains the Lantern " \
       "Garden.\r\nEnd line.\r\n"
TEXT_FILES = {
    "plain.txt": (TEXT.encode("utf-8"), "text",
                  "Line one.\nAfter \x1a the byte, the text goes on.\nMira maintains the "
                  "Lantern Garden.\nEnd line.\n"),
    "utf16.txt": (TEXT.encode("utf-16"), "text",
                  "Line one.\nAfter \x1a the byte, the text goes on.\nMira maintains the "
                  "Lantern Garden.\nEnd line.\n"),
    "guide.md": (("# Garden\r\n\r\n" + TEXT).encode("utf-8"), "markdown",
                 "# Garden\n\nLine one.\nAfter \x1a the byte, the text goes on.\nMira "
                 "maintains the Lantern Garden.\nEnd line.\n"),
    "page.html": (("<html><body>\r\n<p>" + TEXT + "</p>\r\n</body></html>\r\n").encode("utf-8"),
                  "html", None),            # html: checked against parse() of the same bytes
}


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ===================================================================== static check
class EveryLowLevelOpenIsBinary(unittest.TestCase):
    """Every os.open in SK Brain names O_BINARY in its flags (Python's own
    open() is always binary underneath; only os.open descriptors are not)."""

    @staticmethod
    def missing_binary(src, filename="<src>"):
        """[(line, call)] for every low-level open whose flags do not name O_BINARY.
        A flags VARIABLE counts only if every assignment to it in the same
        function names O_BINARY."""
        tree = ast.parse(src, filename)
        bad = {}

        def assigns_in(scope):
            out = {}
            for a in ast.walk(scope):
                if isinstance(a, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and \
                        a.value is not None:
                    for t in (a.targets if isinstance(a, ast.Assign) else [a.target]):
                        if isinstance(t, ast.Name):
                            out.setdefault(t.id, []).append(ast.unparse(a.value))
            return out

        scopes = [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in scopes:                   # innermost function wins (visited last)
            assigns = assigns_in(scope)
            for node in ast.walk(scope):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                f = node.func
                if f.attr == "FileIO":
                    bad[node.lineno] = "io.FileIO"
                    continue
                if not (isinstance(f.value, ast.Name) and f.value.id == "os"):
                    continue
                if f.attr == "openpty":
                    bad[node.lineno] = "os.openpty"
                elif f.attr == "open":
                    flags = node.args[1] if len(node.args) > 1 else next(
                        (k.value for k in node.keywords if k.arg == "flags"), None)
                    if isinstance(flags, ast.Name):
                        vals = assigns.get(flags.id, [])
                        ok = bool(vals) and all("O_BINARY" in v for v in vals)
                    else:
                        ok = flags is not None and "O_BINARY" in ast.unparse(flags)
                    if ok:
                        bad.pop(node.lineno, None)
                    else:
                        bad[node.lineno] = ast.unparse(node)
        return sorted(bad.items())

    def brain_sources(self):
        out = {}
        for n in sorted(os.listdir(BRAIN)):
            if n.endswith(".py"):
                with open(os.path.join(BRAIN, n), encoding="utf-8") as fh:
                    out[n] = fh.read()
        return out

    def test_every_os_open_in_the_brain_has_o_binary(self):
        srcs = self.brain_sources()
        seen = [n for n, s in srcs.items() if "os.open(" in s]
        self.assertEqual(sorted(seen), ["importer.py", "keystore.py"])
        for n, s in srcs.items():
            self.assertEqual(self.missing_binary(s, n), [], n)

    def test_o_binary_is_the_real_flag_on_windows(self):
        if WINDOWS:
            self.assertTrue(getattr(os, "O_BINARY", 0))


# ===================================================================== the importer
class ImportKeepsEveryByte(World):
    def stored(self, s, sid):
        return s.store._db.execute(
            "SELECT text, bytes_tag FROM main.source_versions WHERE source_id=?",
            (sid,)).fetchone()

    def test_read_file_returns_the_exact_bytes(self):
        pdf1 = pdf_with_crlf_and_ctrl_z()
        pdf2, _ = compressed_pdf_with_crlf_and_ctrl_z()
        files = [("a.pdf", pdf1, importer.PDF_MAX_BYTES), ("b.pdf", pdf2, importer.PDF_MAX_BYTES)]
        files += [(n, d, importer.MAX_BYTES) for n, (d, _, _) in TEXT_FILES.items()]
        with text_mode_os(importer) as fake:
            for name, data, limit in files:
                with self.subTest(name):
                    self.assertEqual(importer.read_file(self.write(name, "", raw=data), limit),
                                     data)
        if fake is not None:                    # the emulation saw binary descriptors only
            self.assertTrue(fake.opened)
            self.assertTrue(all(b for _, b in fake.opened), fake.opened)

    def test_pdfs_with_cr_lf_and_ctrl_z_import_with_their_exact_bytes(self):
        s = self.open("ana")
        pdf2, k = compressed_pdf_with_crlf_and_ctrl_z()
        for name, data, want in (("crlf.pdf", pdf_with_crlf_and_ctrl_z(), MG + "\n\n" + CALM),
                                 ("zipped.pdf", pdf2, None)):
            with self.subTest(name), text_mode_os(importer):
                sid = s.import_file(self.write(name, "", raw=data), author_kind="named",
                                    author_name="Ada Guide")["source_id"]
                text, tag = self.stored(s, sid)
                self.assertEqual(tag, hashlib.sha256(data).hexdigest())
                if want is None:                # what the same bytes give, read directly
                    want = importer.parse(data, "pdf").text
                    self.assertTrue(want.startswith(MG + "\n"), want)
                self.assertEqual(text, want)
        c = s.learn(MG, "imported", "Lantern Garden")
        r = s.cite(c, sid, MG)
        self.assertTrue(r["supports"])
        self.assertEqual(r.get("page"), 1)

    def test_txt_md_html_with_cr_lf_and_ctrl_z_decode_completely(self):
        s = self.open("ana")
        for name, (data, fmt, want) in TEXT_FILES.items():
            with self.subTest(name), text_mode_os(importer):
                sid = s.import_file(self.write(name, "", raw=data), author_kind="named",
                                    author_name="Ada Guide")["source_id"]
                text, tag = self.stored(s, sid)
                self.assertEqual(tag, importer.parse(data, fmt).bytes_sha)
                self.assertEqual(text, want if want is not None else importer.parse(data, fmt).text)
                self.assertIn("After \x1a the byte, the text goes on.", text.replace("\n", " "))
                self.assertIn("End line.", text)               # nothing cut at the 0x1A
                self.assertNotIn("\r", text)


# ===================================================================== the key store
class KeysSurviveClosingAndReloading(World):
    N = 64
    AWKWARD = [b"\n" * 32, b"\r\n" * 16, b"\x1a" * 32, b"\r" * 31 + b"\n",
               bytes(range(32)), bytes(range(224, 256)), b"\x1a\r\n" * 10 + b"\n\n"]

    def keys(self):
        """64 keys: the awkward ones first (LF, CR LF, 0x1A), then random ones."""
        real = os.urandom
        queue = list(self.AWKWARD)
        return lambda n: queue.pop(0) if n == 32 and queue else real(n)

    def test_64_keys_created_stored_closed_and_reloaded_unchanged(self):
        d = os.path.join(self.d, "keys")
        made = {}
        with text_mode_os(keystore) as fake, \
                mock.patch.object(keystore.os, "urandom", self.keys()) if fake is None else \
                mock.patch.object(fake, "urandom", self.keys(), create=True):
            for i in range(self.N):
                p = os.path.join(d, "k%02d.key" % i)
                made[p] = keystore.load_or_create_key(p)
        self.assertEqual(len(made), self.N)
        self.assertEqual(len(set(made.values())), self.N)
        self.assertEqual(list(made.values())[:len(self.AWKWARD)], self.AWKWARD)
        self.assertEqual(sorted(os.listdir(d)), sorted(os.path.basename(p) for p in made))
        for p, key in made.items():                         # files closed: read back cold
            with open(p, "rb") as fh:
                blob = fh.read()
            self.assertEqual(keystore.unprotect(blob), key, p)
            if not WINDOWS:
                self.assertEqual(blob, keystore.MAGIC_PLAIN + key)
            with text_mode_os(keystore):
                self.assertEqual(keystore.load_or_create_key(p), key)   # reload: same bytes
                self.assertEqual(keystore.load_or_create_key(p), key)
            with open(p, "rb") as fh:
                self.assertEqual(fh.read(), blob)          # reloading never rewrites it


# ===================================================================== deliberate breaks
class DeliberateBreaks(World):
    """The same checks against copies with O_BINARY removed must FAIL."""

    def test_the_static_check_catches_a_missing_o_binary(self):
        for name in ("importer", "keystore"):
            with open(os.path.join(BRAIN, name + ".py"), encoding="utf-8") as fh:
                src = fh.read()
            self.assertEqual(EveryLowLevelOpenIsBinary.missing_binary(src), [])
            broken = without_binary(src)
            self.assertNotEqual(broken, src)
            self.assertEqual(len(EveryLowLevelOpenIsBinary.missing_binary(broken)), 1, name)
        # other spellings of an unchecked low-level open are caught too
        for src in ("import os\nfd = os.open(p, os.O_RDONLY)\n",
                    "import os\nfd = os.open(p, flags=os.O_RDONLY)\n",
                    "import os\nfd = os.open(p)\n",
                    "import io\nf = io.FileIO(p)\n",
                    "import os\nos.openpty()\n",
                    "import os\ndef f(p):\n    flags = os.O_RDONLY | os.O_BINARY\n"
                    "    flags = os.O_RDONLY\n    return os.open(p, flags)\n",
                    "import os\ndef f(p):\n    return os.open(p, flags)\n"):
            self.assertTrue(EveryLowLevelOpenIsBinary.missing_binary(src), src)

    def test_an_importer_without_o_binary_loses_bytes(self):
        broken, _ = load_copy("importer", without_binary)
        pdf2, _ = compressed_pdf_with_crlf_and_ctrl_z()
        for name, data, fmt in (("a.pdf", pdf_with_crlf_and_ctrl_z(), "pdf"),
                                ("b.pdf", pdf2, "pdf"),
                                *((n, d, f) for n, (d, f, _) in TEXT_FILES.items())):
            with self.subTest(name), text_mode_os(broken):
                p = self.write(name, "", raw=data)
                got = broken.read_file(p, broken.PDF_MAX_BYTES)
                self.assertNotEqual(got, data)                       # bytes lost
                if fmt == "pdf":
                    with self.assertRaises(ImportRefused) as cm:      # the PC's refusal
                        broken.parse(got, "pdf")
                    self.assertIn("damaged or not standard (PdfReadError)", str(cm.exception))
                with text_mode_os(importer):                          # the fixed one keeps them
                    self.assertEqual(importer.read_file(p, importer.PDF_MAX_BYTES), data)

    def test_a_key_store_without_o_binary_damages_keys(self):
        """Emulated: every key holding an LF byte is damaged. Windows (DPAPI: the
        stored blob is ciphertext): among 64 keys at least one blob holds an LF."""
        broken, _ = load_copy("keystore", without_binary)
        d = os.path.join(self.d, "broken_keys")
        if WINDOWS:
            keys = [os.urandom(32) for _ in range(64)]
        else:
            keys = [k for k in KeysSurviveClosingAndReloading.AWKWARD if b"\n" in k]
        damaged = 0
        for i, key in enumerate(keys):
            p = os.path.join(d, "k%02d.key" % i)
            with text_mode_os(broken) as fake, \
                    mock.patch.object(broken.os, "urandom", lambda n, k=key: k) if fake is None \
                    else mock.patch.object(fake, "urandom", lambda n, k=key: k, create=True):
                self.assertEqual(broken.load_or_create_key(p), key)
            try:
                again = keystore.load_or_create_key(p)
            except OSError:
                again = None
            damaged += again != key
        if WINDOWS:
            self.assertGreaterEqual(damaged, 1)
        else:
            self.assertEqual(damaged, len(keys))
            self.assertGreaterEqual(damaged, 4)


# ===================================================================== the Windows report
class ReportListsEveryFailure(unittest.TestCase):
    """The f688383 report cut the failure list at 20 (the whole suite) and the
    tracebacks at 10 (Brain tests), hiding 3 failures. Both sections must now
    list every failing test - here 25, in a throwaway folder of fake tests."""
    N = 25

    def setUp(self):
        import shutil
        import tempfile
        from puzzle_assistant.devtools import brain_windows_check as W
        self.W = W
        self.root = tempfile.mkdtemp(prefix="sk_report_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "tests"))
        self.mod = "test_brain_fake_report_%d" % os.getpid()
        body = ["import unittest", "class Fake(unittest.TestCase):", "    pass"]
        for i in range(self.N):                  # failures and errors, mixed
            body.append("def _t%d(self):\n    %s" % (
                i, "self.fail('boom %d')" % i if i % 2 else "raise ValueError('err %d')" % i))
            body.append("Fake.test_%02d = _t%d" % (i, i))
        with open(os.path.join(self.root, "tests", self.mod + ".py"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n".join(body) + "\n")
        self.addCleanup(sys.modules.pop, self.mod, None)
        saved = list(W.results)
        self.addCleanup(lambda: W.results.__setitem__(slice(None), saved))

    def run_check(self, fn):
        with mock.patch.object(self.W, "HERE", self.root):
            fn()
        name, ok, detail = self.W.results[-1]
        self.assertIs(ok, False)
        return detail

    def assert_all_listed(self, detail):
        head = "all %d failed / erroring tests:\n" % self.N
        self.assertIn(head, detail)
        listed = detail.split(head, 1)[1].split("\n---", 1)[0].splitlines()
        self.assertEqual(len(listed), self.N, listed)          # the list itself, uncut
        for i in range(self.N):
            self.assertEqual(sum("test_%02d " % i in ln for ln in listed), 1, i)
        tracebacks = detail.split("\n---", 1)[1]
        self.assertEqual(tracebacks.count("Traceback (most recent call last)"), self.N)

    def test_the_whole_suite_section_lists_every_failure(self):
        detail = self.run_check(self.W.full_suite)
        self.assertIn("failures=12, errors=13", detail)
        self.assert_all_listed(detail)

    def test_the_brain_tests_section_lists_every_failure(self):
        detail = self.run_check(self.W.unit_tests)
        self.assertIn("failed 12, errors 13", detail)
        self.assert_all_listed(detail)


if __name__ == "__main__":
    unittest.main()
