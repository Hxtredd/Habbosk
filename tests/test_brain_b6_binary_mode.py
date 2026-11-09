"""Windows binary mode for SK Brain's low-level file access (Windows check of f688383).

On Windows ``os.open`` opens a file in TEXT mode unless O_BINARY is given:
reading turns every CR LF into LF and stops at the first 0x1A byte, writing
turns every LF into CR LF. importer.read_file had no O_BINARY, so on Windows
PDFs whose bytes happened to hold CR LF or 0x1A were refused as "damaged"
(PdfReadError), and a 0x1A cut TXT / MD / HTML files short.

CORRECTION (Windows check of 5497fbe): the key store's write is NOT affected. It
opens its descriptor with os.open and then wraps it with os.fdopen(fd, "wb"); CPython's
FileIO (what os.fdopen builds) calls _setmode(fd, O_BINARY) on every descriptor it
wraps (Modules/_io/fileio.c), so the key bytes are never translated, with or without
O_BINARY in os.open. An earlier version of these tests (and the notes) claimed
otherwise; the real Windows run showed 0 of 64 keys damaged by a keystore copy without
O_BINARY. O_BINARY stays in keystore.py as defence in depth. Raw descriptors that are
read or written with os.read / os.write (the importer's read) ARE text mode.

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
        self.fd_path = {}                      # fd -> path it was opened for

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags, mode=0o777, *a, **k):
        binary = bool(flags & self.FAKE_BINARY)
        fd = os.open(path, flags & ~self.FAKE_BINARY, mode, *a, **k)
        self.opened.append((path, binary))
        self.fd_path[fd] = os.path.abspath(path)
        if not binary:
            self.text_fds[fd] = None
            if flags & (os.O_WRONLY | os.O_RDWR):
                self.text_written.add(os.path.abspath(path))
        return fd

    def fdopen(self, fd, *a, **k):
        """CPython's FileIO calls _setmode(fd, O_BINARY): from here on the descriptor is
        binary, whatever flags os.open used (checked on real Windows)."""
        self.text_fds.pop(fd, None)
        self.text_written.discard(self.fd_path.get(fd))
        return os.fdopen(fd, *a, **k)

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
    # UTF-16 stores CR as 0D 00, so only a big-endian U+0D0A (a Malayalam letter) puts the
    # byte pair 0D 0A (CR LF) into the file: that is what Windows text mode would alter
    "utf16be.txt": (b"\xfe\xff" + (TEXT + "Note \u0d0a end.\r\n").encode("utf-16-be"),
                    "text", None),
    "guide.md": (("# Garden\r\n\r\n" + TEXT).encode("utf-8"), "markdown",
                 "# Garden\n\nLine one.\nAfter \x1a the byte, the text goes on.\nMira "
                 "maintains the Lantern Garden.\nEnd line.\n"),
    "page.html": (("<html><body>\r\n<p>" + TEXT + "</p>\r\n</body></html>\r\n").encode("utf-8"),
                  "html", None),            # html: checked against parse() of the same bytes
}


assert b"\r\n" in TEXT_FILES["utf16be.txt"][0] and b"\x1a" in TEXT_FILES["utf16be.txt"][0]


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ===================================================================== static check
RISKY_MODULES = ("os", "io", "posix", "nt", "_io", "codecs", "tempfile", "pathlib", "builtins")
FLAG_NODES = (ast.BinOp, ast.BitOr, ast.Attribute, ast.Call, ast.Constant, ast.Name, ast.Load,
              ast.keyword)


def file_access_problems(src, filename="<src>"):
    """[(line, reason)] for every way of touching a file that Windows text mode could
    change, in the source text of ONE module. Rules (checked on the syntax tree):
      * os.open: its flags are only ORed constants (os.O_*, getattr(os, "O_...", 0)) and
        name O_BINARY as a real reference (a string, masking, ~, & or a conditional does
        not count); a flags variable must obey this at EVERY assignment, and only `|=`
        may change it;
      * open / io.open / codecs.open / os.fdopen / Path.open: the mode is a literal; a
        write or append mode needs "b"; text reads only with encoding="utf-8";
      * no Path.read_text / write_text; no text-mode temporary files; no mkstemp(text=True);
      * no aliased import of os / io / posix / nt / tempfile ..., no `from os import open`
        (or fdopen, read, write), no `x = os`, no getattr(os, <anything but O_...>)."""
    tree = ast.parse(src, filename)
    found = {}

    def bad(node, why):
        found.setdefault(node.lineno, why)

    def is_name(node, *names):
        return isinstance(node, ast.Name) and node.id in names

    def lit(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) \
            else None

    def has_binary(expr):
        for n in ast.walk(expr):
            if isinstance(n, ast.Attribute) and n.attr == "O_BINARY" and is_name(n.value, "os"):
                return True
            if isinstance(n, ast.Call) and is_name(n.func, "getattr") and len(n.args) >= 2 \
                    and is_name(n.args[0], "os") and lit(n.args[1]) == "O_BINARY":
                return True
        return False

    def flag_expr_ok(expr, allow_name=False):
        """only ORs of os.O_* / getattr(os, "O_*", 0) / integers"""
        if isinstance(expr, ast.BinOp):
            return isinstance(expr.op, ast.BitOr) and flag_expr_ok(expr.left, allow_name) and \
                flag_expr_ok(expr.right, allow_name)
        if isinstance(expr, ast.Attribute):
            return is_name(expr.value, "os") and expr.attr.startswith("O_")
        if isinstance(expr, ast.Constant):
            return isinstance(expr.value, int) and not isinstance(expr.value, bool)
        if isinstance(expr, ast.Call):
            f = expr
            return (is_name(f.func, "getattr") and len(f.args) == 3 and is_name(f.args[0], "os")
                    and (lit(f.args[1]) or "").startswith("O_")
                    and isinstance(f.args[2], ast.Constant) and f.args[2].value == 0)
        return allow_name and isinstance(expr, ast.Name)

    def mode_problem(call, mode_index):
        mode = call.args[mode_index] if len(call.args) > mode_index else next(
            (k.value for k in call.keywords if k.arg == "mode"), None)
        enc = next((k.value for k in call.keywords if k.arg == "encoding"), None)
        if mode is None:
            return None if lit(enc) == "utf-8" else "text-mode read without encoding='utf-8'"
        m = lit(mode)
        if m is None:
            return "file mode is not a literal"
        if "b" in m:
            return None
        if set(m) & set("wax+"):
            return "text-mode write"
        return None if lit(enc) == "utf-8" else "text-mode read without encoding='utf-8'"

    # imports and aliases of the modules that open files
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in RISKY_MODULES and a.asname:
                    bad(n, "aliased import of " + a.name)
                if a.name.split(".")[0] in ("posix", "nt", "builtins"):
                    bad(n, "import of " + a.name)
        elif isinstance(n, ast.ImportFrom):
            top = (n.module or "").split(".")[0]
            names = {a.name for a in n.names}
            if top == "os" and names & {"open", "fdopen", "read", "write", "openpty"}:
                bad(n, "from os import " + ", ".join(sorted(names)))
            if top in ("io", "_io", "codecs") and names & {"open", "FileIO"}:
                bad(n, f"from {top} import open")
            if top == "tempfile" and names & {"NamedTemporaryFile", "TemporaryFile",
                                              "SpooledTemporaryFile", "mkstemp"}:
                bad(n, "from tempfile import a temporary-file function")
            if top in ("posix", "nt", "builtins"):
                bad(n, "from " + top + " import ...")
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and \
                n.value.id in RISKY_MODULES:
            bad(n, "alias of the module " + n.value.id)
        elif isinstance(n, ast.Call) and is_name(n.func, "getattr", "hasattr") and n.args and \
                is_name(n.args[0], "os", "io"):
            if not (len(n.args) >= 2 and (lit(n.args[1]) or "").startswith("O_")):
                bad(n, "getattr on os / io other than an O_ flag")

    # flags variables, per function (innermost wins)
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    flags_ok = {}
    for scope in scopes:
        assigns = {}
        for a in ast.walk(scope):
            if isinstance(a, ast.AugAssign) and isinstance(a.target, ast.Name):
                assigns.setdefault(a.target.id, []).append(
                    (a.value, "or") if isinstance(a.op, ast.BitOr) else (None, "other"))
            elif isinstance(a, (ast.Assign, ast.AnnAssign)) and a.value is not None:
                for t in (a.targets if isinstance(a, ast.Assign) else [a.target]):
                    if isinstance(t, ast.Name):
                        assigns.setdefault(t.id, []).append((a.value, "set"))
        for name, vals in assigns.items():
            flags_ok[(id(scope), name)] = vals
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            f = node.func
            if f.attr == "open" and is_name(f.value, "os"):
                flags = node.args[1] if len(node.args) > 1 else next(
                    (k.value for k in node.keywords if k.arg == "flags"), None)
                if isinstance(flags, ast.Name):
                    vals = assigns.get(flags.id, [])
                    # every plain assignment names O_BINARY; `|=` may add other flags;
                    # nothing may mask or replace them any other way
                    ok = bool(vals) and all(v is not None and flag_expr_ok(v)
                                            for v, _ in vals) and \
                        any(k == "set" for _, k in vals) and \
                        all(has_binary(v) for v, k in vals if k == "set")
                else:
                    ok = flags is not None and flag_expr_ok(flags) and has_binary(flags)
                if ok:
                    found.pop(node.lineno, None) if found.get(node.lineno, "").startswith(
                        "os.open") else None
                elif node.lineno not in found or found[node.lineno].startswith("os.open"):
                    found[node.lineno] = "os.open without O_BINARY in plain ORed flags"

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "open":
            why = mode_problem(node, 1)
        elif isinstance(f, ast.Attribute) and f.attr == "open" and is_name(f.value, "io", "codecs"):
            why = mode_problem(node, 1)
        elif isinstance(f, ast.Attribute) and f.attr == "open" and not is_name(f.value, "os"):
            why = mode_problem(node, 0)                       # Path(p).open(mode)
        elif isinstance(f, ast.Attribute) and f.attr == "fdopen" and is_name(f.value, "os"):
            why = mode_problem(node, 1)
        elif isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            why = "Path." + f.attr
        elif isinstance(f, ast.Attribute) and f.attr in (
                "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"):
            m = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "mode"), None)
            why = None if m is None else (
                "temporary file mode is not a literal" if lit(m) is None
                else (None if "b" in lit(m) else "text-mode temporary file"))
        elif isinstance(f, ast.Attribute) and f.attr == "mkstemp" and any(
                k.arg == "text" for k in node.keywords):
            why = "mkstemp(text=...)"
        else:
            continue
        if why:
            bad(node, why)
    return sorted(found.items())


def brain_py_files():
    for dirpath, _, names in os.walk(BRAIN):
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.join(dirpath, n)


class EveryLowLevelOpenIsBinary(unittest.TestCase):
    """Every file access in SK Brain is binary (or, for UTF-8 JSON reads, explicit),
    in every module and sub-package. Python's own open() is binary underneath unless
    given a text mode; os.open descriptors and text modes are what Windows alters."""

    missing_binary = staticmethod(file_access_problems)      # name kept for the break tests

    def test_no_file_access_in_the_brain_can_be_text_mode(self):
        files = list(brain_py_files())
        self.assertGreater(len(files), 20)
        for path in files:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            self.assertEqual(file_access_problems(src, path), [], path)

    def test_only_importer_and_keystore_call_os_open(self):
        using = []
        for path in brain_py_files():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
                   n.func.attr == "open" and isinstance(n.func.value, ast.Name) and
                   n.func.value.id == "os" for n in ast.walk(tree)):
                using.append(os.path.relpath(path, BRAIN))
        self.assertEqual(sorted(using), ["importer.py", "keystore.py"])

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

    def test_the_pdf_reader_process_receives_every_byte(self):
        """The data channel to the isolated reader is binary: a probe process that
        only counts and hashes what arrives on its stdin sees exactly what was sent
        (on Windows a text-mode stdin would stop at the first 0x1A byte)."""
        from puzzle_assistant.brain import pdf_import
        probe = ("import sys, json, hashlib\n"
                 "d = sys.stdin.buffer.read()\n"
                 "sys.stdout.write(json.dumps({'refused': 'probe', 'message': "
                 "'n=%d sha=%s' % (len(d), hashlib.sha256(d).hexdigest())}))\n")
        for data in (pdf_with_crlf_and_ctrl_z(), b"%PDF-1.7\r\n\x1a\r\n" * 500,
                     b"\x1a" + bytes(range(256)) * 40):
            with mock.patch.object(pdf_import, "_BOOT", probe):
                with self.assertRaises(ImportRefused) as cm:
                    pdf_import.read_in_child(data)
            self.assertEqual(str(cm.exception), "n=%d sha=%s" % (
                len(data), hashlib.sha256(data).hexdigest()))

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


class AnUnreadableKeyFileIsNeverReplaced(World):
    """A key file that cannot be read (damaged by the old code, wrong format, another
    Windows user's) must fail loudly and stay byte-identical: a new key made over it
    would orphan everything signed or protected with the old one."""

    def test_unreadable_key_files_raise_and_stay_unchanged(self):
        d = os.path.join(self.d, "damaged")
        os.makedirs(d)
        cases = {"empty": b"", "garbage": b"not an sk key file at all",
                 "dpapi-shaped": keystore.MAGIC_DPAPI + b"\x00\x01" * 40}
        for name, blob in cases.items():
            p = os.path.join(d, name + ".key")
            with open(p, "wb") as fh:
                fh.write(blob)
            with self.subTest(name):
                with self.assertRaises(OSError):
                    keystore.load_or_create_key(p)
                with open(p, "rb") as fh:
                    self.assertEqual(fh.read(), blob)
                self.assertFalse(os.path.exists(p + ".tmp"))
        self.assertEqual(sorted(os.listdir(d)), sorted(n + ".key" for n in cases))


# ===================================================================== deliberate breaks
class DeliberateBreaks(World):
    """The same checks against copies with O_BINARY removed must FAIL."""

    def test_the_static_check_catches_a_missing_o_binary(self):
        for name in ("importer", "keystore"):
            with open(os.path.join(BRAIN, name + ".py"), encoding="utf-8") as fh:
                src = fh.read()
            self.assertEqual(file_access_problems(src), [])
            broken = without_binary(src)
            self.assertNotEqual(broken, src)
            self.assertEqual(len(file_access_problems(broken)), 1, name)
        # every other way of opening a file in text mode (or hiding O_BINARY) is caught too
        flagged = [
            "import os\nfd = os.open(p, os.O_RDONLY)\n",
            "import os\nfd = os.open(p, flags=os.O_RDONLY)\n",
            "import os\nfd = os.open(p)\n",
            "import os\ndef f(p):\n    flags = os.O_RDONLY | os.O_BINARY\n"
            "    flags = os.O_RDONLY\n    return os.open(p, flags)\n",
            "import os\ndef f(p):\n    return os.open(p, flags)\n",
            # O_BINARY hidden: a string, masked, negated, conditional, multiplied
            "import os\nos.open(p, os.O_RDONLY | len('O_BINARY') * 0)\n",
            "import os\nos.open(p, os.O_RDONLY | (getattr(os, 'O_BINARY', 0) & 0))\n",
            "import os\nos.open(p, os.O_RDONLY | (getattr(os, 'O_BINARY', 0) * 0))\n",
            "import os\nos.open(p, os.O_RDONLY | (getattr(os, 'O_BINARY', 0) if p else 0))\n",
            "import os\ndef f(p):\n    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)\n"
            "    flags &= ~getattr(os, 'O_BINARY', 0)\n    return os.open(p, flags)\n",
            "import os\ndef f(p):\n    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0)\n"
            "    flags ^= getattr(os, 'O_BINARY', 0)\n    return os.open(p, flags)\n",
            # aliases of os / os.open
            "import os as _os\n_os.open(p, _os.O_RDONLY | _os.O_BINARY)\n",
            "from os import open as _o\n_o(p, 0)\n",
            "from os import open, read, O_RDONLY\n",
            "import os\nm = os\nm.open(p, m.O_RDONLY)\n",
            "import os\ngetattr(os, 'open')(p, 0)\n",
            "import os\nname = 'open'\ngetattr(os, name)(p, 0)\n",
            "import posix\n",
            "from posix import open\n",
            # text-mode builtin / io / codecs / fdopen / Path
            "open(p, 'w')\n", "open(p, 'a')\n", "open(p, 'r+')\n", "open(p)\n", "open(p, 'r')\n",
            "open(p, mode)\n", "open(p, 'w', encoding='utf-8')\n",
            "open(p, encoding='latin-1')\n",
            "import io\nio.open(p, 'w')\n", "import io\nio.open(p)\n",
            "import codecs\ncodecs.open(p)\n", "from io import open\n",
            "import os\nos.fdopen(fd, 'w')\n", "import os\nos.fdopen(fd)\n",
            "from pathlib import Path\nPath(p).read_text()\n",
            "from pathlib import Path\nPath(p).write_text('x')\n",
            "from pathlib import Path\nPath(p).open('w')\n",
            "import tempfile\ntempfile.NamedTemporaryFile('w')\n",
            "import tempfile\ntempfile.NamedTemporaryFile(mode='w+')\n",
            "import tempfile as t\nt.mkstemp()\n",
            "from tempfile import NamedTemporaryFile\n",
            "import tempfile\ntempfile.mkstemp(text=True)\n",
        ]
        for src in flagged:
            self.assertTrue(file_access_problems(src), src)
        # and the safe spellings (all used by the Brain today) are NOT flagged
        safe = [
            "import os\nos.open(p, os.O_RDONLY | getattr(os, 'O_BINARY', 0))\n",
            "import os\nos.open(p, os.O_WRONLY | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)\n",
            "import os\ndef f(p):\n    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | \\\n"
            "        getattr(os, 'O_NOFOLLOW', 0)\n    return os.open(p, flags)\n",
            "open(p, 'rb')\n", "open(p, 'wb')\n", "open(p, 'rb+')\n", "open(p, 'ab')\n",
            "open(p, encoding='utf-8')\n", "open(p, 'r', encoding='utf-8')\n",
            "import os\nos.fdopen(fd, 'wb')\n",
            "import io\nio.open(p, 'rb', buffering=0)\n",
            "import tempfile\ntempfile.mkdtemp()\n",
            "import tempfile\ntempfile.NamedTemporaryFile()\n",
            "from pathlib import Path\nPath(p).read_bytes()\n",
            "import io\nf = io.FileIO(p)\n",
        ]
        for src in safe:
            self.assertEqual(file_access_problems(src), [], src)

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

    def test_the_key_write_stays_binary_through_fdopen_even_without_o_binary(self):
        """Found by the Windows check of 5497fbe (this test used to assert the opposite and
        failed there): a key store WITHOUT O_BINARY in its os.open still writes and reloads
        every key unchanged, because os.fdopen(fd, "wb") makes the descriptor binary. On
        Windows this runs the real C runtime over 64 random DPAPI-protected keys (about
        half of the stored blobs hold an LF byte); elsewhere the emulation does the same
        for LF / CR LF / 0x1A keys. It documents why the flag in keystore.py is defence in
        depth - and the next test shows what WOULD damage a key."""
        broken, _ = load_copy("keystore", without_binary)
        keys = [os.urandom(32) for _ in range(64)] if WINDOWS else \
            list(KeysSurviveClosingAndReloading.AWKWARD)
        d = os.path.join(self.d, "broken_keys")
        for i, key in enumerate(keys):
            p = os.path.join(d, "k%02d.key" % i)
            with text_mode_os(broken) as fake, \
                    mock.patch.object(broken.os, "urandom", lambda n, k=key: k) if fake is None \
                    else mock.patch.object(fake, "urandom", lambda n, k=key: k, create=True):
                self.assertEqual(broken.load_or_create_key(p), key)
            self.assertEqual(keystore.load_or_create_key(p), key, i)

    def test_a_key_written_with_a_raw_descriptor_in_text_mode_is_damaged(self):
        """What WOULD damage a key: os.write on a descriptor opened without O_BINARY (no
        fdopen to switch it to binary). Emulated on Linux only (on Windows the C runtime
        does this and the previous test's premise is what was checked for real)."""
        if WINDOWS:
            self.skipTest("emulation of the C runtime; real text mode is exercised elsewhere")
        raw, _ = load_copy("keystore", lambda src: without_binary(src).replace(
            '    with os.fdopen(fd, "wb") as fh:\n        fh.write(protect(key))\n'
            '        fh.flush()\n        os.fsync(fh.fileno())\n',
            '    os.write(fd, protect(key))\n    os.fsync(fd)\n    os.close(fd)\n'))
        d = os.path.join(self.d, "raw_keys")
        damaged = 0
        for i, key in enumerate(k for k in KeysSurviveClosingAndReloading.AWKWARD
                                if b"\n" in k):
            p = os.path.join(d, "k%02d.key" % i)
            with text_mode_os(raw) as fake, mock.patch.object(
                    fake, "urandom", lambda n, k=key: k, create=True):
                raw.load_or_create_key(p)
            try:
                damaged += keystore.load_or_create_key(p) != key
            except OSError:
                damaged += 1
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
