# Version-testing record

Newest first. Entries are added, never rewritten. Only results that actually
ran are recorded; "not run" is written out.

### 23ed6b6 - B6 Windows binary-mode fix (`claude/brain-b6`)
- Changed: `puzzle_assistant/brain/importer.py`, `keystore.py` (O_BINARY);
  `devtools/brain_windows_check.py`; new `tests/test_brain_b6_binary_mode.py` (11);
  notes. Commits 90741c3, 23ed6b6 (parent f688383).
- Linux (Claude session, 2026-11-09): whole suite 737 tests OK (0 skipped);
  binary-mode tests 11/11 (with O_BINARY removed: 16 failures, 3 errors);
  deliberate breaks 163/163 caught. Brain fingerprint 2e6025b6fd6d5961.
- Windows 11: not run yet (owner to run BRAIN_WINDOWS_CHECK.bat).
- Render candidate: not applicable (not deployed). Desktop: not applicable.
- Limitations: Windows text mode is emulated on Linux; key files damaged by
  the old code are not repaired.
- Approved: pending (owner's independent review).

### f688383 - B6 integrated (`claude/brain-integration`)
- Linux (Claude session, 2026-11-09): whole suite 726 tests OK; B4 / B5.2 /
  B6 / import / upgrade / PDF / process-guard / citation regressions 318 OK;
  deliberate breaks 151/151.
- Windows 11 (owner's PC, 2026-11-09, Windows-11-10.0.26200, Python 3.14.10,
  fingerprint 2ced25c1876b5886): BRAIN_WINDOWS_CHECK 11 passed, 2 failed.
  Windows-only steps all PASSED (B5.2, B6 sources, B6 PDF, B6 upgrade). Brain
  tests 578: 2 failed, 20 errors; whole suite 726: 3 failed, 20 errors - test
  PDFs refused as damaged (cause: missing O_BINARY, fixed in 23ed6b6). FAILED.
- Render candidate: SK-Online `claude/sk-lab-candidate` 7ed29ac (Brain
  f688383): 420 tests OK, GitHub run passed; deployment by the owner,
  confirmation of the deployed version not recorded.
- Desktop: not applicable (the desktop app does not use the Brain).
- Approved: integration approved by the owner 2026-11-09 on Linux validation;
  Windows validation outstanding.

### 417e0f9 - Desktop SK Assistant v3.3 (release branch)
- Linux (Claude session): 317 tests OK (the commit message says 319 - wrong).
- Windows 11: tested by the owner in normal use; no test suite run recorded.
- Approved: released at the owner's instruction ("release it after you fix it").
