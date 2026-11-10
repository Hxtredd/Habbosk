# SK Assistant v3.4.0 - release candidate checklist

Status: **PUBLISHED 2026-11-10** as release commit `7b96415` (Option B) - see RELEASE_v3.4.md.

| | |
|---|---|
| App code | `211541e` (A-D + fixes from the owner's Windows trial and three independent review rounds) |
| Candidate commit | the commit adding this file, branch `claude/kind-dijkstra-usz63w` |
| Package | `dist/SK_Assistant_v3.4.zip` - 163 files, 11 966 795 bytes, SHA-256 `a853e4ed0bc7dcf889b1ffcbfc3aa12d618ab0dbab03d6c4a28080009adcaad5` |
| Update metadata | `latest.json`: version 3.4.0, zip `dist/SK_Assistant_v3.4.zip`, same size and SHA-256 |
| Release branch | `claude/windows-puzzle-assistant-1lxjat` still `417e0f9` (v3.3.0) - untouched |

## Done
- [x] Scope: only A-D (+ their fixes); provider, model, prompt, thinking, requirements.txt and Go to Room (`world_atlas/navigate.py`) unchanged - checked against v3.3 in the package.
- [x] Version 3.4.0 (`puzzle_assistant/__init__.py`), README title and v3.4 section (timing log: location, content, size, how to switch off), DEVELOPMENT_NOTES.
- [x] Release ZIP = exactly `publish_release.release_files()`: no benchmarks, trial, trial packages, dev tools/tests, TLS test files; bytes identical to the commit; vs v3.3: 5 files added, none removed.
- [x] Full regression: repo 412 OK on Python 3.14.6 (no skips, incl. full-app run on a virtual screen), 411 OK on 3.13; AI tests also on 3.12.
- [x] Inside the release package: 389 OK on 3.14 and 3.13 (13 skipped: TLS tests without their files, git-only check).
- [x] Update path with v3.3's own updater: offers 3.4.0, refuses a damaged download, installs 14 changed files, keeps `.venv`, makes `_previous`; updated tree = package; 389 OK there.
- [x] Deliberate-break check: 29/29 caught, control passes (Python 3.14).
- [x] Independent review (Claude reviewer, not human/ChatGPT): 3 rounds, all findings resolved or documented - `REVIEW_v3.4.md`.
- [x] Owner's Windows: benchmark before/after and real-play trial of A-D (records preserved, e.g. at `80915b1`).
- [x] Trial accounting: 37 = 35 shown + 1 stopped (question changed) + 1 unusable reply.

## Before publishing (owner)
- [x] Windows self-test of the final code (cec97c9 = 8718eb6 app files): PASSED, see windows_selftest_v34/ -: `SK_Assistant_TRIAL_d531c89.zip` covers the transport/cancel code incl. TLS 1.3 tests (211541e adds only `reset_call_info`, tested on Linux 3.12-3.14).
- [x] Final release approval (owner, Option B).

## Publishing (only after approval) - owner to choose
- A) Fast-forward the release branch to the candidate commit (history linear from 417e0f9). Simple; puts the development files (benchmarks, trial, trial packages ~50 MB) on the GitHub default branch too (they never ship in the ZIP).
- B) One release commit on the release branch containing only the release files changed since v3.3 plus `dist/` and `latest.json`. Clean update channel; development history stays on the development branch.
- Then: users' SK offers 3.4.0 on its next check (Settings > Updates); nothing installs without their click.

## Known limitations
- A reused connection that breaks while waiting for the reply may be sent again (Gemini may have handled the first); at most one extra request, counted.
- Requests through urllib (any PC with a proxy, Claude, OpenAI) can't be stopped mid-way; they hold a lane until their 6-8 s limit (bounded: 2 lanes).
- Options that are themselves letters with a letter reply are now rejected as ambiguous (v3.3 took the letter).
- Not in v3.4 (proposed v3.5): open chat-history panel hides the question; PAREJAS sums misread / no solver. Later: exit abort / `_stop` naming.
