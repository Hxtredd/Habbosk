"""Run SK Assistant's automated tests inside the trial folder, with the
isolation guard switched on and a throw-away data folder - so the tests
themselves can't touch the normal SK Assistant either.

    selftest.py          the AI tests (about a minute)
    selftest.py --all    every test (several minutes)

Writes trial_results\SELFTEST_<date-time>.txt: Windows / Python / OpenSSL
versions, the build commit, every test with its result, the failures and the
blocked-access count (test keys only - never the real key, which the tests
are not even allowed to read).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import shutil
import ssl
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard  # noqa: E402

QUICK = ["tests.test_ai", "tests.test_ai_speed_v34", "tests.test_ai_speed_check", "tests.test_express",
         "tests.test_question_marks", "tests.test_qa_memory", "tests.test_trial", "test_windows_tls",
         "tests.test_work"]


class Recorder(unittest.TextTestResult):
    """Also keeps every test's outcome for the results file."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.outcomes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes.append((test.id(), "ok", ""))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes.append((test.id(), "FAIL", self._exc_info_to_string(err, test).strip().splitlines()[-1]))

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes.append((test.id(), "ERROR", self._exc_info_to_string(err, test).strip().splitlines()[-1]))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes.append((test.id(), "skipped", reason))


def system_info() -> dict:
    info = {"platform": platform.platform(), "python": sys.version.split()[0],
            "openssl": ssl.OPENSSL_VERSION, "machine": platform.machine()}
    if sys.platform == "win32":
        info["windows"] = " ".join(x for x in platform.win32_ver() if x)
        try:
            info["windows_edition"] = platform.win32_edition()
        except Exception:
            pass
    try:
        with open(os.path.join(HERE, "BUILD.json"), encoding="utf-8") as fh:
            info["build"] = json.load(fh).get("commit", "?")
    except Exception:
        info["build"] = "unknown"
    return info


def write_report(info, result, blocked, ok, mode) -> str:
    out = os.path.join(guard.TRIAL_ROOT, "trial_results")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"SELFTEST_{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt")
    counts = {k: sum(1 for o in result.outcomes if o[1] == k) for k in ("ok", "FAIL", "ERROR", "skipped")}
    lines = ["SK Assistant TRIAL - self-test results (" + mode + ")",
             *[f"{k}: {v}" for k, v in info.items()],
             f"tests run: {result.testsRun}; ok {counts['ok']}, failed {counts['FAIL']}, "
             f"errors {counts['ERROR']}, skipped {counts['skipped']}",
             f"blocked accesses to the normal SK data: {blocked} (should be 0)",
             "RESULT: " + ("PASSED" if ok else "FAILED"), ""]
    lines += [f"{st:7s} {tid}" + (f"  -- {why}" if why else "") for tid, st, why in result.outcomes]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def main(argv) -> int:
    protected = guard.normal_data_dirs()
    guard.check_layout(protected)
    sandbox = tempfile.mkdtemp(prefix="sk_trial_selftest_")
    g = guard.Guard(protected, allow_read=[],          # tests never need the real key
                    log_path=os.path.join(sandbox, "blocked_access.log"))
    g.install()
    os.environ["SK_ASSISTANT_DATA"] = sandbox
    os.chdir(guard.TRIAL_ROOT)
    sys.path.insert(0, guard.TRIAL_ROOT)
    loader = unittest.defaultTestLoader
    info = system_info()
    print("  ".join(f"{k}: {v}" for k, v in info.items()))
    if "--all" in argv:
        suite = unittest.TestSuite([loader.discover("tests"), loader.loadTestsFromName("test_windows_tls")])
    else:
        suite = loader.loadTestsFromNames(QUICK)
    result = unittest.TextTestRunner(verbosity=1, resultclass=Recorder).run(suite)
    blocked = g.blocked
    print(f"\nBlocked accesses to the normal SK data during the tests: {blocked} (should be 0)")
    shutil.rmtree(sandbox, ignore_errors=True)
    ok = result.wasSuccessful() and blocked == 0
    path = write_report(info, result, blocked, ok, "all tests" if "--all" in argv else "quick")
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    print(f"Results saved: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
