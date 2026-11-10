"""Run SK Assistant's automated tests inside the trial folder, with the
isolation guard switched on and a throw-away data folder - so the tests
themselves can't touch the normal SK Assistant either.

    selftest.py          the AI tests (about a minute)
    selftest.py --all    every test (several minutes)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard  # noqa: E402

QUICK = ["tests.test_ai", "tests.test_ai_speed_v34", "tests.test_ai_speed_check", "tests.test_express",
         "tests.test_question_marks", "tests.test_qa_memory", "tests.test_trial"]


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
    if "--all" in argv:
        suite = loader.discover("tests")
    else:
        suite = loader.loadTestsFromNames(QUICK)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"\nBlocked accesses to the normal SK data during the tests: {g.blocked} (should be 0)")
    shutil.rmtree(sandbox, ignore_errors=True)
    ok = result.wasSuccessful() and g.blocked == 0
    print("SELF-TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
