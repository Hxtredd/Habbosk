"""The isolated SK trial (trial/): guard, launcher, summary, package, and an
end-to-end run of the complete desktop app on a virtual screen.

Everything runs in subprocesses (an audit hook can't be removed) against a
fake "normal SK Assistant" data folder in a temporary HOME, whose files must
stay byte-for-byte identical."""
import base64
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KEY = "AIzaTRIAL-fake-key-000111222333"
PY = sys.executable


def tree(path):
    out = {}
    for root, _d, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, path)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def make_normal_sk(home):
    """A fake everyday SK data folder (Linux location) with real-looking contents."""
    d = os.path.join(home, ".local", "share", "SK Assistant")
    os.makedirs(os.path.join(d, "diagnostics"))
    for name in ("habbosk.db", "atlas.db", "knowledge.db"):
        con = sqlite3.connect(os.path.join(d, name))
        con.execute("CREATE TABLE t (x TEXT)")
        con.execute("INSERT INTO t VALUES ('owner data')")
        con.commit()
        con.close()
    with open(os.path.join(d, "config.json"), "w") as fh:
        json.dump({"ai_provider": "gemini", "text_scale": 1.2, "check_updates": True,
                   "ai_daily_limit": 200}, fh)
    with open(os.path.join(d, "keys.bin"), "wb") as fh:
        fh.write(b"PLAIN:" + base64.b64encode(json.dumps({"ai:gemini": KEY}).encode()))
    with open(os.path.join(d, "diagnostics", "ai_timing.jsonl"), "w") as fh:
        fh.write('{"ts": "2026-11-09T10:00:00", "outcome": "shown"}\n')
    return d


def make_trial_root(base):
    """What the package contains, as a folder (the zip test checks the real zip)."""
    root = os.path.join(base, "SK Assistant TRIAL")
    os.makedirs(root)
    for d in ("puzzle_assistant", "assets", "atlas_input", "tessdata", "trial"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(root, d),
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(ROOT, "requirements.txt"), root)
    return root


def env_for(home):
    e = dict(os.environ)
    e.update({"HOME": home, "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
              "PYTHONDONTWRITEBYTECODE": "1"})
    for k in ("SK_ASSISTANT_DATA", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
              "ALL_PROXY", "all_proxy"):
        e.pop(k, None)
    return e


class TrialBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.normal = make_normal_sk(self.home)
        self.before = tree(self.normal)
        self.root = make_trial_root(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def py(self, code, *args, timeout=120):
        script = os.path.join(self.tmp, "probe.py")
        with open(script, "w") as fh:
            fh.write(textwrap.dedent(code))
        r = subprocess.run([PY, script, self.root, *args], capture_output=True, text=True,
                           env=env_for(self.home), timeout=timeout, cwd=self.tmp)
        return r

    def assertNormalUntouched(self):
        self.assertEqual(tree(self.normal), self.before)


class GuardTests(TrialBase):
    def test_guard_blocks_everything_but_reading_the_key(self):
        r = self.py('''
            import json, os, shutil, sqlite3, sys
            sys.path.insert(0, os.path.join(sys.argv[1], "trial"))
            import guard
            normal = guard.normal_data_dirs()[0]
            g = guard.Guard([normal], allow_read=[os.path.join(normal, "keys.bin")],
                            log_path=os.path.join(sys.argv[1], "blocked.log"))
            link = os.path.join(sys.argv[1], "link")
            os.symlink(normal, link)            # made before the guard (it refuses links into the folder)
            g.install()
            res = {}
            def t(name, fn):
                try:
                    fn(); res[name] = "allowed"
                except PermissionError:
                    res[name] = "blocked"
                except Exception as e:
                    res[name] = "other:" + type(e).__name__
            j = lambda *p: os.path.join(normal, *p)
            t("read key", lambda: open(j("keys.bin"), "rb").read())
            t("write key", lambda: open(j("keys.bin"), "ab"))
            t("read db", lambda: open(j("habbosk.db"), "rb"))
            t("read config", lambda: open(j("config.json")))
            t("sqlite db", lambda: sqlite3.connect(j("habbosk.db")))
            t("sqlite uri ro", lambda: sqlite3.connect("file:" + j("atlas.db") + "?mode=ro", uri=True))
            t("os.open write", lambda: os.open(j("knowledge.db"), os.O_RDWR))
            t("remove", lambda: os.remove(j("atlas.db")))
            t("rename", lambda: os.rename(j("atlas.db"), j("x.db")))
            t("replace into", lambda: os.replace(os.path.join(sys.argv[1], "requirements.txt"), j("r.txt")))
            t("mkdir", lambda: os.mkdir(j("new")))
            t("listdir", lambda: os.listdir(normal))
            t("copy out", lambda: shutil.copy(j("habbosk.db"), sys.argv[1]))
            t("relative path", lambda: (os.chdir(normal), open("habbosk.db", "rb")))
            os.chdir(sys.argv[1])
            t("make a link", lambda: os.symlink(normal, os.path.join(sys.argv[1], "link2")))
            t("through a link", lambda: open(os.path.join(link, "habbosk.db"), "rb"))
            t("diagnostics", lambda: open(j("diagnostics", "ai_timing.jsonl"), "a"))
            t("outside", lambda: open(os.path.join(sys.argv[1], "ok.txt"), "w").write("x"))
            res["blocked"] = g.blocked
            print(json.dumps(res))
        ''')
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        allowed = {"read key", "outside"}
        for name, how in res.items():
            if name == "blocked":
                continue
            self.assertEqual(how, "allowed" if name in allowed else "blocked", name)
        self.assertEqual(res["blocked"], len(res) - 1 - len(allowed))
        self.assertNormalUntouched()
        with open(os.path.join(self.root, "blocked.log")) as fh:
            self.assertEqual(len(fh.readlines()), res["blocked"])

    def test_overlapping_folders_refuse_to_start(self):
        r = self.py('''
            import os, sys
            sys.path.insert(0, os.path.join(sys.argv[1], "trial"))
            import guard
            for prot in (os.path.dirname(guard.TRIAL_ROOT), guard.TRIAL_DATA):
                try:
                    guard.check_layout([prot]); print("ACCEPTED", prot)
                except guard.IsolationError:
                    print("refused")
            guard.check_layout([os.path.join(os.path.expanduser("~"), "elsewhere")]); print("ok")
        ''')
        self.assertEqual(r.stdout.split(), ["refused", "refused", "ok"], r.stderr)

    def test_normal_folder_location(self):
        sys.path.insert(0, os.path.join(ROOT, "trial"))
        import guard
        self.assertEqual(guard.normal_data_dirs({"APPDATA": r"C:\Users\x\AppData\Roaming"}, "win32"),
                         [os.path.join(r"C:\Users\x\AppData\Roaming", "SK Assistant")])
        d = guard.normal_data_dirs({"APPDATA": "A", "SK_ASSISTANT_DATA": "D:/skdata"}, "win32")
        self.assertEqual(d[1], "D:/skdata")                  # a custom normal folder is protected too


class LauncherTests(TrialBase):
    PREP = '''
        import json, os, sys
        sys.path.insert(0, os.path.join(sys.argv[1], "trial"))
        import launcher, guard
        cfg, g, ctx = launcher.prepare()
        from puzzle_assistant import secrets, updater, config
        from puzzle_assistant.qa.memory import Memory
        from puzzle_assistant.world_atlas.db import Atlas
        out = {"paths": ctx["paths"], "settings": ctx["settings"],
               "key_ok": secrets.get_key("ai:gemini") == os.environ.get("EXPECT"),
               "has_key": secrets.has_key("ai:gemini"),
               "check_updates": cfg.check_updates, "timing_log": cfg.ai_timing_log,
               "provider": cfg.ai_provider, "text_scale": cfg.text_scale}
        secrets.set_key("ai:gemini", "NEW-KEY")
        out["key_after_set"] = secrets.get_key("ai:gemini") == os.environ.get("EXPECT")
        out["update_check"] = updater.check()
        try:
            updater.install("x.zip"); out["install"] = "ran"
        except RuntimeError:
            out["install"] = "refused"
        m = Memory(); m.close()
        a = Atlas(); a.close() if hasattr(a, "close") else None
        out["blocked"] = g.blocked
        out["trial_files"] = sorted(os.listdir(guard.TRIAL_DATA))
        print(json.dumps(out))
    '''

    def run_prepare(self):
        r = subprocess.run([PY, "-c", textwrap.dedent(self.PREP), self.root], capture_output=True,
                           text=True, env=dict(env_for(self.home), EXPECT=KEY), timeout=120,
                           cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1]), r

    def test_prepare_isolates_and_reuses_the_key_read_only(self):
        out, r = self.run_prepare()
        trial_data = os.path.join(self.root, "trial_data")
        for what, p in out["paths"].items():
            self.assertTrue(os.path.realpath(p).startswith(os.path.realpath(trial_data)), what)
        self.assertTrue(out["key_ok"] and out["has_key"] and out["key_after_set"])
        self.assertEqual((out["check_updates"], out["timing_log"]), (False, True))
        self.assertEqual((out["provider"], out["text_scale"]), ("gemini", 1.2))   # settings copied
        self.assertEqual(out["settings"], "copied from the normal SK (read-only)")
        self.assertIsNone(out["update_check"])
        self.assertEqual(out["install"], "refused")
        self.assertEqual(out["blocked"], 0)
        self.assertIn("habbosk.db", out["trial_files"])
        self.assertIn("config.json", out["trial_files"])
        self.assertNotIn("keys.bin", out["trial_files"])
        self.assertNormalUntouched()
        for root, _d, files in os.walk(trial_data):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(KEY.encode(), fh.read(), f)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        # second start keeps the trial's own settings
        out2, _ = self.run_prepare()
        self.assertEqual(out2["settings"], "kept")
        self.assertNormalUntouched()

    def test_trial_inside_the_normal_folder_does_not_start(self):
        bad = os.path.join(self.normal, "SK Assistant TRIAL")
        shutil.copytree(self.root, bad)
        r = subprocess.run([PY, os.path.join(bad, "trial", "launcher.py")], capture_output=True, text=True,
                           env=env_for(self.home), timeout=60, cwd=self.tmp)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("did NOT start", r.stdout)
        shutil.rmtree(bad)
        self.assertNormalUntouched()


class SummaryTests(unittest.TestCase):
    def test_summary(self):
        sys.path.insert(0, os.path.join(ROOT, "trial"))
        import summary
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "diagnostics"))
        rows = [{"ts": "a", "outcome": "shown", "total_ms": 600.0, "timed": True, "calls": 1, "reused": True},
                {"ts": "b", "outcome": "shown", "total_ms": 3000.0, "timed": False, "calls": 1},
                {"ts": "c", "outcome": "no_answer", "error": "overload", "calls": 1, "total_ms": 900.0},
                {"ts": "d", "outcome": "stale", "error": "stale", "calls": 1}]
        with open(os.path.join(tmp, "diagnostics", "ai_timing.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        s = summary.summarize(summary.read_log(tmp), tmp)
        self.assertEqual(s["outcomes"], {"shown": 2, "no_answer": 1, "stale": 1})
        self.assertEqual(s["no_answer_reasons"], {"overload": 1})
        self.assertEqual(s["slow_answers_over_2_5_s"], 1)
        self.assertEqual(s["e_evidence_share"], round(2 / 3, 3))
        self.assertEqual(s["requests_sent"], 4)
        self.assertEqual(s["blocked_accesses_to_normal_sk"], 0)
        self.assertIn("typical", summary.text(s))
        shutil.rmtree(tmp)


@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, "tools", "build_trial.py"))
                     and os.path.isdir(os.path.join(ROOT, ".git")),
                     "builds the package from git: only in the source repository, not inside the package")
class PackageTests(unittest.TestCase):
    def test_package_leaves_out_everything_that_touches_the_normal_install(self):
        out = subprocess.run([PY, os.path.join(ROOT, "tools", "build_trial.py"), "HEAD"], capture_output=True,
                             text=True, cwd=ROOT, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr)
        info = json.loads(out.stdout)
        path = os.path.join(ROOT, info["zip"])
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                build = json.loads(z.read("SK Assistant TRIAL/trial/BUILD.json"))
                start = z.read("SK Assistant TRIAL/TRIAL_START.bat")
        finally:
            if os.environ.get("SK_KEEP_TRIAL_ZIP") != "1":
                os.remove(path)
        self.assertTrue(all(n.startswith("SK Assistant TRIAL/") for n in names))
        short = {n.split("/", 1)[1] for n in names}
        for bad in ("INSTALL.bat", "CREATE_SHORTCUTS.bat", "RUN.bat", "latest.json", "tools/create_shortcuts.ps1",
                    "tools/publish_release.py", "BRAIN_TEST_BENCH.bat", "BRAIN_WINDOWS_CHECK.bat"):
            self.assertNotIn(bad, short)
        self.assertFalse(any(s.startswith("dist/") for s in short))
        for good in ("TRIAL_SETUP.bat", "TRIAL_START.bat", "TRIAL_SUMMARY.bat", "TRIAL_README.txt",
                     "trial/launcher.py", "trial/guard.py", "puzzle_assistant/qa/ai_jobs.py",
                     "puzzle_assistant/diagnostics.py", "requirements.txt", "tessdata/spa.traineddata"):
            self.assertIn(good, short)
        self.assertEqual(build["commit"], info["commit"])
        self.assertIn(b"\r\n", start)                      # Windows line endings for .bat


def _xvfb():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("xvfb-run") and shutil.which("tesseract"))


@unittest.skipUnless(_xvfb() and sys.platform != "win32", "needs Tkinter, xvfb-run and Tesseract (Linux)")
class FullAppEndToEnd(TrialBase):
    def test_full_trial_app(self):
        result = os.path.join(self.tmp, "result.json")
        harness = os.path.join(HERE, "trial_e2e_harness.py")
        r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", PY, harness, self.root, result],
                           capture_output=True, text=True, env=env_for(self.home), timeout=180, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        with open(result, encoding="utf-8") as fh:
            res = json.load(fh)
        # startup: isolated, titled, settings copied, guard live in the real app process
        self.assertEqual(res["title"], "SK Assistant - TRIAL (test version)")
        self.assertEqual(res["probe"], "blocked")
        self.assertEqual(res["blocked_total"], 1)          # only the deliberate probe
        self.assertNormalUntouched()
        # the key reached the (fake) provider, read from the normal SK's keys.bin
        self.assertTrue(res["posts"] and all(p["key"] == KEY for p in res["posts"]))
        self.assertGreaterEqual(len(res["posts"]), 4)
        statuses = [s["status"] for s in res["statuses"]]
        self.assertIn("memory", statuses)                  # library question from memory
        self.assertIn("ai", statuses)                      # answered by the AI
        self.assertIn("unknown", statuses)                 # 503 -> no answer
        self.assertNotIn("game", statuses)
        # clean shutdown with a hanging request in flight
        self.assertLess(res["shutdown_s"], 5.0)
        self.assertLess(res["threads_s"], 3.0)                # threads ended before exit
        # the trial's own timing log
        log = os.path.join(self.root, "trial_data", "diagnostics", "ai_timing.jsonl")
        with open(log, encoding="utf-8") as fh:
            text = fh.read()
        rows = [json.loads(line) for line in text.splitlines()]
        outcomes = [r_["outcome"] for r_ in rows]
        self.assertEqual(outcomes.count("shown"), 1, outcomes)
        self.assertGreaterEqual(outcomes.count("stale"), 2, outcomes)    # changed + shutdown
        self.assertEqual([r_["error"] for r_ in rows if r_["outcome"] == "no_answer"], ["overload"])
        shown = next(r_ for r_ in rows if r_["outcome"] == "shown")
        self.assertTrue(shown["timed"])
        self.assertEqual(shown["category"], "PREGUNTAS EXPRESS")
        for word in ("océano", "Océano", "África", "boleto", KEY):
            self.assertNotIn(word, text)
        # trial data really is the trial's
        files = os.listdir(os.path.join(self.root, "trial_data"))
        for f in ("habbosk.db", "atlas.db", "config.json", "diagnostics"):
            self.assertIn(f, files)
        with open(os.path.join(self.root, "trial_data", "config.json")) as fh:
            self.assertFalse(json.load(fh)["check_updates"])
        for root, _d, fs in os.walk(os.path.join(self.root, "trial_data")):
            for f in fs:
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(KEY.encode(), fh.read(), f)
        # the summary tool on the real trial log
        s = subprocess.run([PY, os.path.join(self.root, "trial", "summary.py")], capture_output=True, text=True,
                           env=env_for(self.home), timeout=60)
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("blocked accesses to the normal SK data: 1", s.stdout)
        self.assertNormalUntouched()


if __name__ == "__main__":
    unittest.main()
