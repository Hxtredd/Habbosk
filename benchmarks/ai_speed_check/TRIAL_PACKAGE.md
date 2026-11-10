# Isolated full desktop trial of A-D - package record

Development build only: not a release, version unchanged (3.3.0), latest.json
and the release branch untouched, the in-app updater never sees it.

| | |
|---|---|
| Package | `trial_dist/SK_Assistant_TRIAL_172d63b.zip` (top folder "SK Assistant TRIAL", 171 files, 12 000 041 bytes) |
| SHA-256 | `5750b8ce9d80027ae8601da8b3e322f96602f359e614a5a4d2d12c381276c2da` |
| Code | commit `172d63b` = A-D (`e9cee60`) + closed-memory counting fix + trial tooling (`f03b38a`, `8fc56b2`) + Windows test fix (`172d63b`) |
| Built by | `tools/build_trial.py 172d63b` |
| Replaces | `SK_Assistant_TRIAL_8fc56b2.zip` (SHA-256 b4ffdec1...): its self-test failed on the owner's PC, see below |

## Isolation (what makes it safe)

1. Its own data folder `trial_data\` (SK's SK_ASSISTANT_DATA); every database,
   settings and log path is checked before the window opens.
2. Audit-hook guard (`trial/guard.py`): any open / SQLite connection /
   file-system change inside `%APPDATA%\SK Assistant` (and a custom
   SK_ASSISTANT_DATA of the normal app) is refused and logged - except
   reading `keys.bin`.
3. Key: read-only from the normal `keys.bin` when needed; saving keys off.
4. Settings: copied once, read-only, from the normal `config.json`; updates off.
5. Left out of the package: INSTALL.bat (it would re-point the Desktop icon),
   CREATE_SHORTCUTS.bat, create_shortcuts.ps1, RUN*.bat, BRAIN_*.bat, dist/,
   latest.json, publish_release.py.
6. Same single-instance lock as the normal app: only one runs at a time.

## Automated checks (Linux cloud container; Windows not yet run)

| Check | Result |
|---|---|
| Full suite, repo, Python 3.13 | 383 OK, 1 skipped (full-app test needs Tkinter) |
| Quick self-test from the extracted package, guard on, 3.13 / 3.12 | 102 OK each; 0 blocked |
| Full self-test from the extracted package, guard on, 3.13 | 383 OK, 2 skipped; 0 blocked |
| Full-app end-to-end on Xvfb (real window, engine, capture, OCR, memory, World Atlas, A-D, timing log; fake Gemini over real sockets) | 8/8 passed under load; also passed inside the package self-test (3.12) |
| Guard: 19 kinds of access (read/write/SQLite/URI/os.open/rename/replace/mkdir/listdir/copy/relative/symlink/diagnostics) | all refused except reading keys.bin and files outside |
| Fake "normal SK" folder after every test | byte-identical |

End-to-end timeline verified: express question -> slow AI request stopped
when a memory question replaced it (stale); AI answer shown; HTTP 503 ->
"no answer" (overload); request hanging during window close -> stopped,
window closed in < 0.1 s, threads ended < 3 s; the key reached only the
fake provider; no question text or key in the timing log or trial data.

## Found while testing (pre-existing in v3.3, not caused by A-D, not fixed in the app)

- On Linux/Xvfb the app can abort at Python exit ("terminate called without
  an active exception") when the engine / explorer threads are still in
  OpenCV / OCR: v3.3 12/20, v3.4 10/20 under load, 0/20 when waiting for the
  threads. The trial launcher waits for them (<= 3 s). Not seen reported on
  Windows; unknown there.
- `Engine` and `ExplorerThread` keep an Event named `_stop`, which hides
  `threading.Thread._stop`: `join()` / `is_alive()` raise TypeError on
  Python <= 3.12 (3 existing tests fail on 3.12 for v3.3 and v3.4 alike;
  3.13 / 3.14 unaffected - the owner uses 3.14).

## Owner's first Windows setup (8fc56b2): self-test failed - test defect, guard held

TRIAL_SETUP's self-test reported one failure on the owner's PC (Windows,
Python 3.14). Cause: tests/test_trial.py made its fake "normal SK" folder
only at the Linux location and did not set APPDATA, so on Windows its test
subprocesses used the owner's REAL %APPDATA%\SK Assistant as the folder to
protect. In every one of those processes the guard was on for that real
folder, so no write, delete, rename or database access to it was possible.
What did happen: one launcher test read config.json and keys.bin there
read-only (settings copied into a temporary trial folder that is deleted
afterwards; the key compared in memory, never printed or saved) and failed
on the key comparison; the "trial inside the normal folder" test could open
a trial window from a temporary folder. Fixed in 172d63b: fake folder at
both platforms' locations, and every trial test refuses to run unless the
protected folder is inside its own temp folder.

## Limitations

- Nothing of this has run on Windows yet: the overlay, DPAPI key reading,
  the single-instance lock, the .bat files and Windows file paths in the
  guard are verified only by their Linux equivalents.
- The trial starts with only the 12 starter questions (it never reads the
  normal memory), so more questions go to the AI than in normal play, and
  questions learned in the trial stay in the trial.
- The guard covers Python code; native libraries (Tesseract, OpenCV, Tk)
  never receive paths into the normal data folder from SK.
- Before a release, trial/, tools/build_trial.py and the trial tests must be
  excluded from the release package (publish_release.py packs every tracked
  file).
