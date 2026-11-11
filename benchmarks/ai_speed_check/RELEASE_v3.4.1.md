# SK Assistant v3.4.1 - published (owner-approved: "A just release it", 2026-11-11)

| | |
|---|---|
| Release commit | `e6160494d2b76e7d42e6243544715429c4325682` (parent `7b96415`, v3.4.0) - fast-forward, no force |
| Release branch | `claude/windows-puzzle-assistant-1lxjat` = `e616049` |
| Package | `dist/SK_Assistant_v3.4.1.zip` - 165 files, 13 732 828 bytes, SHA-256 `bd3f26b88ab3dea328837f09593d0ed2f8260dcb864bd4f6755bc7bfcf91293d` |
| latest.json | 3.4.1, same file, size and SHA-256 |
| Fix | money puzzle: no double count when the PC is briefly busy (owner's round: 129 shown, 128 correct) - development commit `97209be` |

Changed vs v3.4.0 (11 paths): money.py, engine.py (money loop timing only),
tests/test_money.py + the owner's round (money panel crop) + panel image,
version 3.4.1, README, DEVELOPMENT_NOTES, tools/publish_release.py (patch
releases keep their full number in the ZIP name), package, latest.json.
Unchanged: AI code, provider/model, Go to Room, requirements.

Validation: full suite in the release checkout 394 OK (Python 3.14.6);
package = release list, identical to the checkout; v3.4's own updater
(local and live from GitHub): offers 3.4.1, verifies the download, refuses a
damaged one, installs 9 changed files, keeps .venv, makes _previous.
Not tested on Windows before release - the owner's decision.
