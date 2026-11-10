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
