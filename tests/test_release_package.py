"""What goes into the release ZIP (tools/publish_release.py, v3.4): the app,
its tests and assets - never the development-only benchmarks, the isolated
trial, their packages and the tests that need them."""
import importlib.util
import os
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location("publish_release", os.path.join(ROOT, "tools", "publish_release.py"))
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

DEV_ONLY = ["benchmarks/ai_speed_check/ai_speed_check.py", "benchmarks/ai_speed_check/baseline/BASELINE.md",
            "benchmarks/ai_speed_check/trial_A-D/RESULTS.md", "trial/launcher.py", "trial/guard.py",
            "trial/TRIAL_START.bat", "trial_dist/SK_Assistant_TRIAL_09ca4da.zip", "tools/build_trial.py",
            "tools/deliberate_breaks_v34.py", "tests/test_trial.py", "tests/trial_e2e_harness.py",
            "tests/test_ai_speed_check.py", "dist/SK_Assistant_v3.3.zip", "latest.json", ".gitignore",
            ".gitattributes"]
SHIPPED = ["app.py", "INSTALL.bat", "RUN.bat", "README.txt", "requirements.txt",
           "puzzle_assistant/diagnostics.py", "puzzle_assistant/qa/ai_jobs.py",
           "puzzle_assistant/qa/transport.py", "puzzle_assistant/qa/ai.py", "puzzle_assistant/engine.py",
           "tests/test_ai_speed_v34.py", "tests/test_ai.py", "tests/test_release_package.py",
           "tools/publish_release.py", "tessdata/spa.traineddata", "assets/seed_questions.json"]


class ReleaseContents(unittest.TestCase):
    def test_development_files_are_left_out(self):
        kept = set(pr.release_files(DEV_ONLY + SHIPPED + [""]))
        self.assertEqual(kept, set(SHIPPED))

    def test_the_real_tree(self):
        if not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("only in the source repository (the release package has no .git)")
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                               check=True).stdout.split("\n")
        kept = pr.release_files(files)
        for f in kept:
            self.assertFalse(f.startswith(("benchmarks/", "trial/", "trial_dist/", "dist/")), f)
            self.assertNotIn(f, pr.EXCLUDE_EXACT)
        for f in files:
            if f.startswith("puzzle_assistant/"):
                self.assertIn(f, kept)
        # every test that ships must not need a development-only file
        for f in kept:
            if f.startswith("tests/") and f.endswith(".py"):
                with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                    text = fh.read()
                for dev in ("benchmarks", "ai_speed_check", "trial_e2e_harness", "import launcher",
                            "build_trial"):
                    if f == "tests/test_release_package.py":
                        continue
                    self.assertNotIn(dev, text, f"{f} needs {dev}")


if __name__ == "__main__":
    unittest.main()
