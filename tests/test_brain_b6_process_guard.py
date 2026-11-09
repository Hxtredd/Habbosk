"""B6 process guard - the ONE owner-approved exception to the B4 rule "no
subprocess in the Brain": puzzle_assistant/brain/pdf_import.py may start SK's
own PDF reader, and nothing else may start any process.

Static checks (on the source, so they hold for every code path) plus runtime
checks (on the call actually made):
  * subprocess is imported only by pdf_import.py, as `import subprocess`, never
    aliased and never `from subprocess import ...`;
  * pdf_import.py uses only subprocess.run / subprocess.TimeoutExpired /
    subprocess.CREATE_NO_WINDOW - no Popen, call, check_call, check_output,
    getoutput, getstatusoutput, no subprocess object passed around;
  * exactly ONE process-launch call in the whole Brain package; it is
    subprocess.run([sys.executable, "-I", "-c", _BOOT, ROOT], ...), _BOOT is the
    fixed text that runs pdf_reader.main(), shell is never passed, and a
    timeout is always passed - and enforced finite (0 < t <= MAX_TIMEOUT)
    before anything is started;
  * no other way to start a program or load code dynamically anywhere in the
    Brain: os.system / popen / spawn* / exec* / posix_spawn* / startfile / fork,
    multiprocessing, concurrent.futures processes, pty, __import__,
    importlib.import_module, eval, exec, compile; ctypes only in keystore.py
    (B5.2: Windows key protection).
This is not an operating-system sandbox (see pdf_import.py)."""
import ast
import math
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from puzzle_assistant.brain import pdf_import  # noqa: E402
from puzzle_assistant.brain.importer import ImportRefused  # noqa: E402

BRAIN = os.path.join(ROOT, "puzzle_assistant", "brain")
EXPECTED_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); "
                 "from puzzle_assistant.brain import pdf_reader; sys.exit(pdf_reader.main())")
ALLOWED_ATTRS = {"run", "TimeoutExpired", "CREATE_NO_WINDOW"}
LAUNCH_NAMES = {"Popen", "call", "check_call", "check_output", "run", "getoutput",
                "getstatusoutput", "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe",
                "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp",
                "execlpe", "execv", "execve", "execvp", "execvpe", "posix_spawn", "posix_spawnp",
                "startfile", "fork", "forkpty"}
FORBIDDEN_MODULES = ("multiprocessing", "concurrent.futures", "pty", "pexpect", "importlib",
                     "runpy", "code", "codeop")


def trees():
    for name in sorted(os.listdir(BRAIN)):
        if name.endswith(".py"):
            with open(os.path.join(BRAIN, name), encoding="utf-8") as fh:
                yield name, ast.parse(fh.read(), name)


def module_const(tree, name):
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and \
                isinstance(n.targets[0], ast.Name) and n.targets[0].id == name:
            return n.value
    return None


class Static(unittest.TestCase):
    def test_subprocess_is_imported_only_by_pdf_import_plainly(self):
        seen = []
        for name, tree in trees():
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name == "subprocess" or a.name.startswith("subprocess."):
                            seen.append((name, a.name, a.asname))
                elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == \
                        "subprocess":
                    seen.append((name, "from " + n.module, None))
        self.assertEqual(seen, [("pdf_import.py", "subprocess", None)])

    def test_pdf_import_uses_only_run_and_two_constants(self):
        tree = dict(trees())["pdf_import.py"]
        uses = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id == "subprocess":
                uses.append(n)
        attrs = set()
        parents = {}
        for p in ast.walk(tree):
            for c in ast.iter_child_nodes(p):
                parents[c] = p
        for n in uses:
            par = parents.get(n)
            if isinstance(par, ast.Attribute) and par.value is n:
                attrs.add(par.attr)
            elif isinstance(par, ast.Call) and isinstance(par.func, ast.Name) and \
                    par.func.id == "getattr" and par.args and par.args[0] is n and \
                    isinstance(par.args[1], ast.Constant):
                attrs.add(par.args[1].value)
            else:
                self.fail("subprocess used other than as subprocess.<name> (line %d)" % n.lineno)
        self.assertTrue(attrs <= ALLOWED_ATTRS, attrs - ALLOWED_ATTRS)

    def launch_calls(self):
        out = []
        for name, tree in trees():
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                attr = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(
                    f, ast.Name) else None
                base = f.value.id if isinstance(f, ast.Attribute) and isinstance(
                    f.value, ast.Name) else None
                if attr in LAUNCH_NAMES and base in ("subprocess", "os", None):
                    if base is None and attr not in ("Popen", "check_output", "check_call",
                                                     "getoutput", "system", "popen"):
                        continue               # e.g. a local function called run()
                    out.append((name, base, attr, n))
        return out

    def test_exactly_one_launch_and_it_is_subprocess_run(self):
        calls = self.launch_calls()
        self.assertEqual([(a, b, c) for a, b, c, _ in calls],
                         [("pdf_import.py", "subprocess", "run")])

    def test_the_launch_is_the_fixed_isolated_reader(self):
        call = self.launch_calls()[0][3]
        argv = call.args[0]
        self.assertIsInstance(argv, ast.List)
        self.assertEqual(len(argv.elts), 5)
        exe, flag_i, flag_c, boot, root = argv.elts
        self.assertTrue(isinstance(exe, ast.Attribute) and exe.attr == "executable" and
                        isinstance(exe.value, ast.Name) and exe.value.id == "sys")
        self.assertEqual((ast.literal_eval(flag_i), ast.literal_eval(flag_c)), ("-I", "-c"))
        self.assertTrue(isinstance(boot, ast.Name) and boot.id == "_BOOT")
        self.assertTrue(isinstance(root, ast.Name) and root.id == "ROOT")
        tree = dict(trees())["pdf_import.py"]
        self.assertEqual(ast.literal_eval(module_const(tree, "_BOOT")), EXPECTED_BOOT)
        self.assertEqual(pdf_import._BOOT, EXPECTED_BOOT)
        self.assertEqual(pdf_import.ROOT, os.path.abspath(ROOT))
        kws = {k.arg: k.value for k in call.keywords}
        self.assertNotIn("shell", kws)
        self.assertNotIn("executable", kws)
        self.assertNotIn("preexec_fn", kws)
        self.assertIn("timeout", kws)
        self.assertTrue(isinstance(kws["timeout"], ast.Name) and kws["timeout"].id == "timeout")
        self.assertEqual(len(call.args), 1)     # (**kw: see the Windows-flag test below)

    def test_no_shell_anywhere(self):
        for name, tree in trees():
            for n in ast.walk(tree):
                if isinstance(n, ast.keyword) and n.arg == "shell":
                    self.fail("shell= in %s line %d" % (name, n.value.lineno))

    def test_no_other_way_to_start_programs_or_load_code(self):
        bad = []
        for name, tree in trees():
            for n in ast.walk(tree):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                        [n.module or ""]
                    for m in mods:
                        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES):
                            bad.append((name, m))
                        if m.split(".")[0] == "ctypes" and name != "keystore.py":
                            bad.append((name, m))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and \
                        n.func.id in ("__import__", "eval", "exec", "compile"):
                    bad.append((name, n.func.id))
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and \
                        n.value.id == "os" and n.attr in LAUNCH_NAMES:
                    bad.append((name, "os." + n.attr))
        self.assertEqual(bad, [])

    def test_the_only_extra_keywords_come_from_the_windows_flag(self):
        src = open(os.path.join(BRAIN, "pdf_import.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        call = self.launch_calls()[0][3]
        stars = [k for k in call.keywords if k.arg is None]
        self.assertLessEqual(len(stars), 1)
        for k in stars:
            self.assertTrue(isinstance(k.value, ast.Name) and k.value.id == "kw")
        # kw only ever gets creationflags
        for n in ast.walk(tree):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and \
                    n.value.id == "kw" and isinstance(n.ctx, ast.Store):
                self.assertEqual(ast.literal_eval(n.slice), "creationflags")


class Runtime(unittest.TestCase):
    def test_non_finite_or_missing_timeouts_start_nothing(self):
        with mock.patch.object(pdf_import.subprocess, "run",
                               side_effect=AssertionError("started")) as run:
            for t in (None, 0, -1, math.inf, math.nan, float("1e9"), True, "30",
                      pdf_import.MAX_TIMEOUT + 1):
                with self.subTest(timeout=t):
                    with self.assertRaises(ImportRefused):
                        pdf_import.read_in_child(b"%PDF-1.7", timeout=t)
            run.assert_not_called()

    def test_the_call_actually_made(self):
        seen = {}

        def fake(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            raise pdf_import.subprocess.TimeoutExpired(argv, kw.get("timeout"))
        with mock.patch.object(pdf_import.subprocess, "run", side_effect=fake):
            with self.assertRaises(ImportRefused):
                pdf_import.read_in_child(b"%PDF-1.7")
        argv, kw = seen["argv"], seen["kw"]
        self.assertEqual(argv[:4], [sys.executable, "-I", "-c", EXPECTED_BOOT])
        self.assertEqual(argv[4], os.path.abspath(ROOT))
        self.assertNotIn("shell", kw)
        self.assertTrue(math.isfinite(kw["timeout"]) and 0 < kw["timeout"] <= 600)
        self.assertEqual(set(kw) - {"input", "capture_output", "timeout", "env",
                                    "creationflags"}, set())
        self.assertTrue(set(kw["env"]) <= {"SYSTEMROOT", "PATH", "TEMP", "TMP", "HOME",
                                           "LANG", "SystemRoot", "Path", "Temp", "Tmp",
                                           "path", "temp", "tmp", "home", "lang"})

    def test_parse_pdf_passes_its_timeout_through(self):
        seen = {}

        def fake(argv, **kw):
            seen["t"] = kw["timeout"]
            raise pdf_import.subprocess.TimeoutExpired(argv, kw["timeout"])
        with mock.patch.object(pdf_import.subprocess, "run", side_effect=fake):
            with self.assertRaises(ImportRefused):
                pdf_import.parse_pdf(b"%PDF-1.7", timeout=7)
        self.assertEqual(seen["t"], 7)


if __name__ == "__main__":
    unittest.main()
