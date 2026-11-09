# Continuous version validation

Every meaningful development version of SK Assistant is tested, traceable and
recoverable. Rules (also in `CLAUDE.md` section 9):

| When | Who | What |
|---|---|---|
| after every implementation change | brain-builder | the test modules for what changed |
| every pushed commit on a `claude/brain-*` branch | GitHub Actions (`.github/workflows/linux-tests.yml`) | whole Linux suite + version record (artifact) |
| meaningful checkpoints | test-engineer | independent run, new tests, deliberate breaks |
| every milestone | test-engineer | complete regression against ALL earlier milestones (`tests/regression_manifest.json`) + full deliberate-break run |
| milestone boundaries and any security-sensitive change | security-auditor | independent security check |
| every integration candidate | Lead | complete test report (`docs/reviews/<milestone>/testing.md`) before the owner decides |
| after integration | Lead | re-run on the integrated commit; record it |

## Platforms - reported separately, never inferred

| Platform | How it is tested | Who runs it |
|---|---|---|
| Linux | whole suite (GitHub Actions on every push; the Lead / test-engineer at checkpoints) | automatic / Claude |
| Windows 11 | `BRAIN_WINDOWS_CHECK.bat` (Windows-only checks + whole suite) and `VERSION_TEST.bat` (whole suite + record) on the owner's PC | the owner |
| Render candidate | SK-Online's own tests + GitHub run for the candidate commit; after the owner deploys, the lab's version page must show that commit | Claude prepares, the owner deploys and confirms |
| Desktop SK Assistant | the release branch's suite when a change reaches the desktop app (today the desktop app does not use the Brain) | Claude; Windows behaviour by the owner |

A platform "passed" only when its tests ran on that platform and the result
is in the record. Emulations (for example Windows text mode on Linux) are
tests, not platform validation.

## The version-testing record

`versions.md` - one entry per tested version, newest first, added and never
rewritten (a correction is a new entry). Each entry:

- exact Git commit; version and milestone; changed components;
- tests executed, with ran / passed / failed / errors / skipped;
- deliberate-break results (caught / total);
- platform (one entry per platform);
- known limitations;
- approved: yes (owner, date) / no / pending.

`tools/version_record.py` writes the measured part of an entry
(`version_test_record.md` / `.json`) on Linux, in GitHub Actions and on
Windows; the commit comes from git, or from the `COMMIT` file in a downloaded
zip. The Brain code fingerprint is the same one BRAIN_WINDOWS_CHECK prints.

## The permanent regression suite

`tests/regression_manifest.json` lists every earlier capability's test modules
with a floor (the number of tests they had). `tests/test_regression_manifest.py`
fails if a listed module disappears, cannot load, drops below its floor, or if
a new Brain test module is not listed. Raising floors and adding modules is
expected; lowering one is a reviewed change with its reason in `changes`.
