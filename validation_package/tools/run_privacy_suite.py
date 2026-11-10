"""Step 2: run the privacy regression suite from the verified source, in a private test area.

Everything the tests write goes into the `test_area` folder of this package. Network
connections (other than loopback) are refused. Nothing outside this package is read or
written on purpose: the data folder, temp folder and the user-profile variables all point
into test_area."""
import os
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import verify_package  # noqa: E402

MODULES = [
    "tests.test_brain_audience_privacy",     # F1 / F5: unverified claims, place names, aliases
    "tests.test_brain_audience_details",     # F2: source titles / dates / quotes
    "tests.test_brain_shareable",            # Response.shareable() / BrainAnswer.shareable()
    "tests.test_brain_shareable_labels",     # step labels, values and sentences (reviewer's concern)
    "tests.test_brain_b6",                    # B6 incl. ChallengeAudience (personal passages)
    "tests.test_brain_reason",               # the reasoning the audience is computed from
    "tests.test_brain_b6_process_guard",     # the static guards over the Brain code
]
FULL = "--full" in sys.argv

MINIMUM = [10, 8, 18, 14, 4, 1, 4, 12, 16]       # fewest tests each line below may match

# requirement -> test-id prefixes that must exist, run and all pass
REQUIREMENTS = [
    ("Personal challenge text never appears in a shared response",
     ["tests.test_brain_b6.ChallengeAudience",
      "tests.test_brain_shareable.RealResponses.test_a_personal_challenge_makes_the_response_unshareable"]),
    ("Personal unverified claims never leak into a shared response",
     ["tests.test_brain_audience_privacy.UnverifiedClaimsInAnswers",
      "tests.test_brain_audience_privacy.MultiStepAnswers"]),
    ("Personal source titles / dates / quotes never leak",
     ["tests.test_brain_audience_details.MigratedOwnerSourceDetails",
      "tests.test_brain_audience_details.OrdinaryShareDoesNotTriggerTheSourceRule",
      "tests.test_brain_shareable.MigratedLibraryEndToEnd"]),
    ("Personal aliases and typo corrections never leak",
     ["tests.test_brain_audience_privacy.PlaceNamesInStepLabels",
      "tests.test_brain_audience_privacy.ReverseQuestions",
      "tests.test_brain_audience_privacy.UnderstoodAsEchoesOnlyTheAskersWords",
      "tests.test_brain_audience_privacy.NothingOfTheAskersOwnRowsInASharedResponse"]),
    ("shareable() removes unsafe text from every relevant field",
     ["tests.test_brain_shareable.Contaminated.test_nothing_personal_survives",
      "tests.test_brain_shareable.Contaminated.test_what_is_removed",
      "tests.test_brain_shareable.Answers.test_hits_trace_question",
      "tests.test_brain_shareable.MoreFailClosed.test_removed_names_are_found_after_accents_quotes_spacing_and_truncation"]),
    ("The original response is never changed by shareable()",
     ["tests.test_brain_shareable.Contaminated.test_the_original_is_unchanged_and_not_shared_with_the_copy"]),
    ("Shared responses keep legitimate shared information and citations",
     ["tests.test_brain_shareable.Contaminated.test_what_is_kept",
      "tests.test_brain_shareable.RealResponses.test_a_shared_response_is_cleaned_and_the_asker_view_is_whole",
      "tests.test_brain_shareable.RealResponses.test_a_response_for_someone_without_material_is_cleaned_too",
      "tests.test_brain_audience_details.GrantedDetailsStayShared"]),
    ("Step labels, values and sentences hold only shared words",
     ["tests.test_brain_shareable_labels"]),
    ("Unknown or unexpected fields fail closed",
     ["tests.test_brain_shareable.FailClosed",
      "tests.test_brain_shareable.MoreFailClosed",
      "tests.test_brain_shareable.MoreAnswers.test_unknown_field_and_enums"]),
]


class Recorder(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.outcome = {}

    def _rec(self, test, what):
        self.outcome[test.id()] = what

    def addSuccess(self, test):
        super().addSuccess(test); self._rec(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err); self._rec(test, "FAIL")

    def addError(self, test, err):
        super().addError(test, err); self._rec(test, "ERROR")

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self._rec(test, "skipped")


def main():
    log = []

    def say(s=""):
        print(s, flush=True)
        log.append(s)

    ok, head = verify_package.verify(quiet=False)
    if not ok:
        return 1
    say("SK Brain - privacy validation (exact commit " + head["source commit"][:7] + ")")
    say("=" * 60)
    say(common.python_banner())
    if common.python_warning():
        say(common.python_warning())
    env = common.isolated_environment("suite")
    common.apply_environment(env)
    common.block_network()
    selftest = common.network_selftest()
    say("Network block self-test: " + ("ok" if selftest else "NOT WORKING"))
    if not selftest:
        say("RESULT: FAIL   the network block is not working; nothing was run")
        return 1
    say("Private test area : " + common.AREA)
    say("Data folder       : " + env["SK_ASSISTANT_DATA"])
    say("Network           : blocked (loopback only)")
    say("")
    os.chdir(common.SOURCE)
    sys.path.insert(0, common.SOURCE)
    sys.path.insert(0, os.path.join(common.SOURCE, "tests"))
    from puzzle_assistant import config as _cfg
    import tempfile
    here = os.path.normcase(os.path.realpath(common.AREA))
    where = {"config.DATA_DIR": str(_cfg.DATA_DIR), "temp folder": tempfile.gettempdir(),
             "home folder": os.path.expanduser("~"), "APPDATA": os.environ.get("APPDATA", "")}
    inside = True
    for k, v in where.items():
        real = os.path.normcase(os.path.realpath(v))
        try:
            good = os.path.commonpath([real, here]) == here
        except ValueError:
            good = False
        inside &= good
        say(f"  {k:16s}: {v}   [{'inside the test area' if good else 'OUTSIDE - STOP'}]")
    if not inside:
        say("RESULT: FAIL   a path points outside the test area; nothing was run")
        return 1
    from puzzle_assistant.devtools import brain_windows_check as bwc
    fp = bwc.code_fingerprint()
    say("Brain source fingerprint: " + fp + "   (expected " + head["brain fingerprint"] + ")")
    fp_ok = fp == head["brain fingerprint"]
    say("")

    t0 = time.time()
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    if FULL:
        suite.addTests(loader.discover(os.path.join(common.SOURCE, "tests"),
                                       top_level_dir=common.SOURCE))
    else:
        for m in MODULES:
            suite.addTests(loader.loadTestsFromName(m))
    runner = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0,
                                     resultclass=Recorder)
    res = runner.run(suite)
    secs = time.time() - t0

    by_mod = {}
    for tid, what in res.outcome.items():
        m = ".".join(tid.split(".")[:2])
        by_mod.setdefault(m, {"pass": 0, "FAIL": 0, "ERROR": 0, "skipped": 0})[what] += 1
    say("Tests by module")
    for m in sorted(by_mod):
        c = by_mod[m]
        say(f"  {m:46s} pass {c['pass']:4d}  fail {c['FAIL']}  error {c['ERROR']}  skipped {c['skipped']}")
    say("")
    say("What the protections cover")
    all_ok = True
    assert len(REQUIREMENTS) == len(MINIMUM)
    for (title, prefixes), least in zip(REQUIREMENTS, MINIMUM):
        matched = {t: w for t, w in res.outcome.items() if any(t.startswith(p) for p in prefixes)}
        missing = [p for p in prefixes if not any(t.startswith(p) for t in res.outcome)]
        good = len(matched) >= least and not missing and \
            all(w == "pass" for w in matched.values())
        all_ok &= good
        say(f"  [{'PASS' if good else 'FAIL'}] {title}  ({len(matched)} tests)")
        if missing:
            say("         not found: " + ", ".join(missing))
    say("")
    n_skip = sum(1 for w in res.outcome.values() if w == "skipped")
    for tr, name in ((res.failures, "FAILURE"), (res.errors, "ERROR")):
        for test, tb in tr:
            say(f"{name}: {test.id()}")
            say("    " + tb.strip().splitlines()[-1][:300])
    # the source must still be byte-for-byte the commit (tests must not have changed it)
    odd = common.remove_compiled(common.SOURCE)
    if odd:
        say("unexpected files in __pycache__: " + ", ".join(odd[:5]))
    again, _ = verify_package.verify(quiet=True)
    say(f"Ran {res.testsRun} tests in {secs:.1f}s: {res.testsRun - len(res.failures) - len(res.errors) - n_skip} passed, "
        f"{len(res.failures)} failed, {len(res.errors)} errors, {n_skip} skipped")
    if n_skip:
        for t, w in sorted(res.outcome.items()):
            if w == "skipped":
                say("  skipped: " + t)
    say("Source unchanged after the run: " + ("yes" if again else "NO"))
    passed = res.wasSuccessful() and all_ok and fp_ok and again and selftest and not odd
    if not fp_ok:
        say("The Brain fingerprint does not match: this is not the expected code.")
    say("")
    say("RESULT: " + ("PASS" if passed else "FAIL") + "   commit " + head["source commit"]
        + ("   (FULL suite)" if FULL else "   (privacy suite)"))
    say("Platform: " + common.python_banner())
    try:
        with open(os.path.join(common.PKG, "privacy_check_report.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(log) + "\n")
        print("Report written to: " + os.path.join(common.PKG, "privacy_check_report.txt"))
    except OSError as e:
        print("could not write the report:", e)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
