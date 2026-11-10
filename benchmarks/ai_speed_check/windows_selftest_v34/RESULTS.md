# Final Windows check of the v3.4 candidate (owner's PC, 2026-11-10)

| | |
|---|---|
| System | Windows 11 10.0.26200 (Home/Core, AMD64), Python 3.14.10, OpenSSL 3.5.9 |
| Package | `SK_Assistant_TRIAL_cec97c9.zip`, SHA-256 cffe312c... verified by the owner; app + test files identical to the candidate `8718eb6` (144 files compared) |
| Candidate | `8718eb6` - release files, `dist/SK_Assistant_v3.4.zip` (SHA-256 a853e4ed...) and `latest.json` unchanged since |

## Self-test (TRIAL_SETUP.bat) - PASSED
131 run, 129 OK, 0 failed, 0 errors, 2 skipped (Linux-only full-app run;
git-only package test), 0 blocked accesses to the normal SK data.
- TLS 1.3 cancellation on Windows sockets: RealTLS13 12/12 OK (fresh and
  reused connection, no socket call from the cancelling thread, TLS 1.3 used).
- Key protection on the wire: KeyNeverInClear 2/2 OK (normal request and five
  requests stopped mid-way: only TLS records, never the test key).
- Question change / cancellation / reconnection / shutdown: EngineCancellation,
  EngineLoop, Scheduler, ConnectionReuse, RealSockets, ShutdownCounting all OK.

## Live check against the real Gemini (TRIAL_START.bat, library rounds) 12:10-12:16
28 requests: 24 answers shown (typical 1.01 s, slowest 10% 1.95 s, slowest
7.88 s), 4 no answer (3 x HTTP 503 "overload" from Google, 1 timeout), kept
connection reused 27x, reopened 0x, 0 blocked accesses. No question change
happened during a request, so cancellation was not exercised live (covered by
the self-test). Google was busier than in the 2026-11-09 trial (0 overloads
then): 2 slow + 4 overload/timeout = 21% of AI questions - evidence for E.
