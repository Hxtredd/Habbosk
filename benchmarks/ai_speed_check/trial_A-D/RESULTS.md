# Owner's Windows trial of A-D (package 09ca4da) - results

| | |
|---|---|
| Package | `SK_Assistant_TRIAL_09ca4da.zip` (SHA-256 ccf6fd78...), self-test PASSED on the owner's PC (Windows, Python 3.14), 0 blocked accesses |
| Play | 2026-11-09 20:14-20:25 local, library rounds only (Preguntas express not available) |
| Raw | `TRIAL_SUMMARY_20261109-202615.txt` / `.json` (numbers only) |

| Measure | Result |
|---|---|
| AI requests | 37 sent: 35 answers shown, 1 stopped because the question changed (B), 1 unusable reply |
| Time the player waited (asked -> shown) | typical **0.82 s**, slowest 10% 1.38 s, slowest 2.0 s |
| Slow (> 2.5 s) / overload / timeout | **0 / 0 / 0** |
| Waiting behind another request | max 0.4 ms (never) |
| Kept connection | reused 35x, reopened 0x |
| Blocked accesses to the normal SK data | **0** |

Notes
- "Waited" includes the screen-check delay (up to ~0.1-0.15 s) on top of the
  request itself (benchmark: ~0.65 s), so 0.82 s is consistent.
- The unusable reply cannot be traced to a question (the log holds no text,
  by design); a likely candidate is a PAREJAS question with misread sums
  (known existing reading problem, see below).
- E (backup request for slow answers): no evidence in this session (0 slow,
  0 overload). Stays deferred.

Existing problems found during the trial (present in v3.3, not caused by A-D):
1. An open "Historial del chat" panel is taken for the question window, so
   the real question is not detected (qa/screen.locate_question returns the
   first box column; the chat rows look like options).
2. PAREJAS with sums: the exact math reader is skipped for pairs
   (`None if lay.pairs`), general OCR misreads × and ÷ ("nx12", "70:7",
   "84 = 12"), and there is no built-in pairs solver, so garbled sums go to
   the AI. The exact reader read 7 of 8 boxes of the owner's screenshot.
