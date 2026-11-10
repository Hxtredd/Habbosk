"""Step 5: a small, targeted Windows run (no whole suite). The two key-store tests that are
skipped on Linux (real Windows DPAPI, real Windows rename) MUST run and pass here, together with
the focused Brain tests for the code changed since commit 68b500f (shareable(), the citation
subject, the respond() citation change, the guard that pins which store calls respond() may use).
Same isolation as the other steps: private test area, network blocked, source verified."""
import os
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import verify_package  # noqa: E402

MODULES = [
    "tests.test_brain_keystore_hardening",   # includes the two Windows-only tests
    "tests.test_brain_respond",              # respond() / citations
    "tests.test_brain_b4_guards",            # respond() may only use the store calls it is allowed
    "tests.test_brain_shareable",            # Response.shareable() / BrainAnswer.shareable()
    "tests.test_brain_shareable_labels",     # step labels, values, sentences; citation subject
]
MUST_RUN_ON_WINDOWS = [
    "tests.test_brain_keystore_hardening.KeyLength.test_windows_dpapi_keys_of_the_wrong_length_are_refused",
    "tests.test_brain_keystore_hardening.NeverOverwrites.test_real_windows_rename_refuses_an_existing_key",
]


class Recorder(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.outcome = {}

    def addSuccess(self, test):
        super().addSuccess(test); self.outcome[test.id()] = "pass"

    def addFailure(self, test, err):
        super().addFailure(test, err); self.outcome[test.id()] = "FAIL"

    def addError(self, test, err):
        super().addError(test, err); self.outcome[test.id()] = "ERROR"

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self.outcome[test.id()] = "skipped: " + reason


def main():
    log = []

    def say(s=""):
        print(s, flush=True)
        log.append(s)

    ok, head = verify_package.verify(quiet=False)
    if not ok:
        return 1
    say("SK Brain - targeted validation (exact commit " + head["source commit"][:7] + ")")
    say("=" * 60)
    say(common.python_banner())
    if common.python_warning():
        say(common.python_warning())
    try:
        import pypdf
        say("pypdf: " + pypdf.__version__)
    except ImportError:
        say("pypdf is NOT installed; run the STEP bat file (it sets up .venv-check).")
        return 1
    env = common.isolated_environment("targeted")
    common.apply_environment(env)
    common.block_network()
    if not common.network_selftest():
        say("RESULT: FAIL   the network block is not working; nothing was run")
        return 1
    here = os.path.normcase(os.path.realpath(common.AREA))
    import tempfile
    os.chdir(common.SOURCE)
    sys.path.insert(0, common.SOURCE)
    sys.path.insert(0, os.path.join(common.SOURCE, "tests"))
    from puzzle_assistant import config as _cfg
    for k, v in (("config.DATA_DIR", str(_cfg.DATA_DIR)), ("temp folder", tempfile.gettempdir())):
        real = os.path.normcase(os.path.realpath(v))
        try:
            good = os.path.commonpath([real, here]) == here
        except ValueError:
            good = False
        say(f"  {k:16s}: {v}   [{'inside the test area' if good else 'OUTSIDE - STOP'}]")
        if not good:
            say("RESULT: FAIL   a path points outside the test area; nothing was run")
            return 1
    from puzzle_assistant.devtools import brain_windows_check as bwc
    fp = bwc.code_fingerprint()
    say("Brain source fingerprint: " + fp + "   (expected " + head["brain fingerprint"] + ")")
    fp_ok = fp == head["brain fingerprint"]
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for m in MODULES:
        suite.addTests(loader.loadTestsFromName(m))
    t0 = time.time()
    res = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0,
                                  resultclass=Recorder).run(suite)
    by_mod = {}
    for tid, what in res.outcome.items():
        m = ".".join(tid.split(".")[:2])
        c = by_mod.setdefault(m, {"pass": 0, "FAIL": 0, "ERROR": 0, "skipped": 0})
        c["skipped" if what.startswith("skipped") else what] += 1
    say("")
    for m in sorted(by_mod):
        c = by_mod[m]
        say(f"  {m:46s} pass {c['pass']:4d}  fail {c['FAIL']}  error {c['ERROR']}  skipped {c['skipped']}")
    say("")
    windows = sys.platform == "win32"
    must_ok = True
    for t in MUST_RUN_ON_WINDOWS:
        what = res.outcome.get(t, "not found")
        short = t.split(".")[-1]
        if windows:
            good = what == "pass"
            must_ok &= good
            say(f"  [{'PASS' if good else 'FAIL'}] Windows-only test ran and passed: {short}"
                + ("" if good else f"   ({what})"))
        else:
            say(f"  [note] not Windows, so this test is skipped as expected: {short} ({what})")
    for test, tb in res.failures + res.errors:
        say("FAILED: " + test.id())
        say("    " + tb.strip().splitlines()[-1][:300])
    skipped_other = [t for t, w in res.outcome.items()
                     if w.startswith("skipped") and t not in MUST_RUN_ON_WINDOWS]
    for t in skipped_other:
        say("  skipped: " + t + "   " + res.outcome[t])
    common.remove_compiled(common.SOURCE)
    again, _ = verify_package.verify(quiet=True)
    n_skip = sum(1 for w in res.outcome.values() if w.startswith("skipped"))
    say(f"Ran {res.testsRun} tests in {time.time() - t0:.1f}s: "
        f"{res.testsRun - len(res.failures) - len(res.errors) - n_skip} passed, "
        f"{len(res.failures)} failed, {len(res.errors)} errors, {n_skip} skipped")
    say("Source unchanged after the run: " + ("yes" if again else "NO"))
    passed = res.wasSuccessful() and fp_ok and again and must_ok and not skipped_other
    say("")
    say("RESULT: " + ("PASS" if passed else "FAIL") + "   targeted validation, commit " + head["source commit"])
    say("Platform: " + common.python_banner())
    try:
        with open(os.path.join(common.PKG, "targeted_check_report.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(log) + "\n")
        print("Report written to: " + os.path.join(common.PKG, "targeted_check_report.txt"))
    except OSError as e:
        print("could not write the report:", e)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
