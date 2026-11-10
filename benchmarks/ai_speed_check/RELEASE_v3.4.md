# SK Assistant v3.4.0 - published (owner-approved, Option B)

| | |
|---|---|
| Approval | owner, 2026-11-10: publish v3.4 as one clean release commit (Option B) |
| Release commit | `7b964157ec9eb4a372cf4b494cef72c4c126c552` |
| Parent | `417e0f972b589777c6036813ec94d6b3ab2aae87` (v3.3.0 release head) - single parent, fast-forward push, no force |
| Release branch | `claude/windows-puzzle-assistant-1lxjat` = `7b96415` (was `417e0f9`) |
| Source candidate | `8718eb640229ae9fce4c246177f9bfb37cf39de7` (development branch) |
| Package | `dist/SK_Assistant_v3.4.zip` - 163 files, 11 966 795 bytes, SHA-256 `a853e4ed0bc7dcf889b1ffcbfc3aa12d618ab0dbab03d6c4a28080009adcaad5` |
| latest.json | version 3.4.0, `dist/SK_Assistant_v3.4.zip`, same size and SHA-256 |

## Contents of the release commit (vs v3.3, 16 paths)
Modified: DEVELOPMENT_NOTES.md, README.txt, latest.json, puzzle_assistant/__init__.py,
config.py, engine.py, qa/ai.py, ui.py, tests/test_ai.py, tools/publish_release.py.
Added: puzzle_assistant/diagnostics.py, qa/ai_jobs.py, qa/transport.py,
tests/test_ai_speed_v34.py, tests/test_release_package.py.
Replaced: dist/SK_Assistant_v3.3.zip -> dist/SK_Assistant_v3.4.zip.
No development files (benchmarks, trial, trial packages, development tests,
test certificates); non-release files left exactly as in v3.3.

## Validation before the push
- All 163 release files byte-identical (git blobs) to the candidate 8718eb6.
- Package entries identical to a fresh checkout of the release commit.
- Package SHA-256 / size = latest.json; parent = v3.3 head; one parent.
- Full suite in the release checkout, Python 3.14.6: 389 OK (13 skipped:
  TLS tests without their development-only files, git-only check).

## Validation after the push (live update channel)
- raw.githubusercontent.com latest.json for the release branch: 3.4.0, a853e4ed...
- v3.3's own updater, real network: check() offers 3.4.0; download() verified
  size and checksum; install() into a throw-away v3.3 copy: 163 files equal to
  the package, version 3.4.0, .venv kept, _previous backup made; suite there:
  389 OK (Python 3.14.6).

Not part of v3.4: E (backup request), v3.5 question-reading fixes, Brain
integration, exit crash / _stop issue.
