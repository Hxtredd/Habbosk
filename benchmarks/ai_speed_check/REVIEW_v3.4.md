# v3.4 release candidate - independent review and resolutions

Reviewer: an independent Claude reviewer agent (read-only, given the code and
the scope, not the author's conclusions). Not a human or ChatGPT review.

## Round 1 - candidate 83ce54f

| # | Severity | Finding | Verified by the author | Resolution |
|---|---|---|---|---|
| 1 | blocker | Cancelling did not stop a request over TLS 1.3 (session tickets make the socket readable with no reply data; the following read blocked; on Windows nothing can wake it). B not effective on Windows against Gemini. | yes - reviewer's repro: TLS 1.3 request ran 4.0 s after cancel, TLS 1.2 stopped at 0.6 s | The whole reply is read in 0.1 s steps with non-blocking reads (`transport._PollingRaw`); an empty TLS read means "keep waiting". Repro now stops at 0.61 s (TLS 1.3 and 1.2). New tests over a real local TLS 1.3 server with Windows-like sockets; they fail on 83ce54f. |
| 2 | should-fix (security) | `SSLSocket.shutdown()` from the cancelling thread clears the TLS state first; a concurrent send could go out unencrypted, key included. | yes - reviewer's forced interleaving showed the key in clear | Nothing is done to the socket from the cancelling thread any more (CancelToken only sets a flag). Test: cancelling makes no shutdown/close call from the cancelling thread. |
| 3 | should-fix | Daily counter read-then-write could lose counts with two lanes. | traced | `_count` under a lock; test with two lanes x 150 counts = 300. Overshoot of the daily limit by at most one request per extra lane remains (status is checked before sending). |
| 4 | should-fix | A reused connection could resend a POST that Gemini already handled; the second send was not counted. | traced | Resend only if no reply byte came back; a second send is counted. Tests: reply broken off -> not resent; reopened -> counted twice. |
| 5 | minor | D: "a pie" / "A pie." read as letter A; uneven case handling; a bare number accepted as text. | yes | Ambiguity judged loosely (case, edge punctuation), text after a letter must be that option, only text replies count. Behaviour change vs v3.3: options that are themselves letters with a letter reply are now rejected as ambiguous. |
| 6 | minor | `select` ValueError surfaced as "bad reply" and skipped cleanup. | traced | Raised as OSError (connection) inside the step-wise reader. |
| 7 | minor | Timing-log line could be written twice (unlocked flag). | traced | `AiJob.claim_log()` under a lock; test with 8 threads. |
| 8 | note | The time under an AI answer now includes waiting. | - | Intended (approved plan A: "the time shown becomes the real total"); in README. |

Test gaps named by the reviewer (real TLS, counter concurrency, broken-off
reply, D case/punctuation) are covered by the new tests; the deliberate-break
check was extended accordingly.

## Round 2 - candidate 04825b0

Reviewer confirmed: TLS 1.3 cancel fixed (0.60-0.64 s, fresh and reused,
Windows-like sockets; 0.40 s mid-chunked-body; stalled body ends at its
limit); no socket call from the cancelling thread; keep-alive, chunked,
partial reply, idle-close and timeout restore correct.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | should-fix | D: a reply that is exactly the text of the option its own letter names was rejected when the text starts with "letter + space" ("A veces" as option A) - a regression vs v3.3 | Accepted when it is exactly (and loosely only) that option; tests for "A veces", "A pie", "B pie" |
| 2 | minor | Connection details of the previous request logged for a failed one | Reset at the start of every request and kept current on failures; test |
| 3 | minor | A resend that then stopped counted once | Counted twice when it reached the provider |
| 4 | minor | Counter lock per AIAnswerer (Settings "Test AI" has its own) | One module-wide lock |
| 5 | minor | Test TLS certificate was a CA with its key shipped in the release | Throw-away CA (key destroyed after signing) + CA:FALSE server certificate; tests/fixtures/tls/ left out of the release ZIP (its tests skip without it) |
| 6 | note | SSLWantWriteError waited for readability | Waits for writability |
| 7 | suspicion | Python 3.14 untested | Full suite on Python 3.14.6 (OpenSSL 3.5): 411 OK, no skips (incl. the full-app run) |

Found by the author's own runs after round 2: on Python 3.12 a TLS server that
closes a kept connection without the TLS goodbye gives SSLEOFError, which was
not treated as "closed in the meantime" (no reopen) - added SSLEOFError /
SSLZeroReturnError to the reopen-once errors (still only on a reused
connection with no reply byte received).
