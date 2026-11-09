# Official pre-optimization AI baseline (SK Assistant v3.3)

Approved by the owner as the reference for AI changes A-D.

| | |
|---|---|
| SK version | v3.3.0, release commit `417e0f9` (AI code unchanged since) |
| Tool | SK AI Speed Check 1.0, commit `dc9ed32` |
| Run | owner's Windows 11 PC, Python 3.14.10, 2026-11-09 18:05-18:16 local time |
| Provider / model | Google Gemini, `gemini-3.5-flash-lite` (SK's own choice; newest stable Flash-Lite) |
| Questions | 20 with known answers: 12 game-confirmed starter questions (7 about the game's own world) + 8 general knowledge, SK's exact request format, no learned facts |
| Raw files | `ai_speed_report_20261109-181603.txt` / `.json` (no key, no personal data) |

## Results

| Comparison | first | median | p90 | slowest | right | errors |
|---|---|---|---|---|---|---|
| **SK today (new connection each time)** | 0.77 s | **0.77 s** | 3.79 s | 7.07 s | **14/20** | 4 |
| reused connection | 0.65 s | 0.65 s | 0.84 s | 1.57 s | 16/20 | 2 |
| streamed (10 questions) | 0.55 s first text | 0.61 s | 0.78 s | 0.81 s | 8/10 | 1 |
| thinking "minimal" | 0.70 s | 0.70 s | 0.88 s | 2.57 s | 16/20 | 1 |
| gemini-3.1-flash-lite (reused) | 0.78 s | 0.78 s | 7.29 s | 13.89 s | 16/20 | 2 |
| gemini-2.5-flash-lite | refused: HTTP 404 "no longer available to new users" | | | | 0/20 | 20 |

Connection set-up (TCP + TLS) on the owner's PC: median 25 ms.
Thinking tokens: 0 in every run (the model does not think by default).

## Findings

- Over all 70 requests to gemini-3.5-flash-lite: 4 failures not caused by SK's
  parsing (2x HTTP 503 "high demand", 1 hang to the 20 s limit, 1 malformed JSON)
  and 3 slow answers (2.6, 5.3, 7.1 s): about 1 request in 10.
- "¿Qué letra sigue?" (options U, X, W, V): the model replied with the option
  TEXT "V" instead of its letter "D" in 4 of 4 runs; SK discards it
  ("answer 'V' is not an option"). Fixed by change D.
- Accuracy when nothing failed: 16/20 (general 11/13, game world 5/7).
  The sample is small: a difference of 1-2 answers is not meaningful.
- No model, thinking or streaming change is justified by this data.

## Accuracy reference for A-D

The "after" run must reach at least the baseline on the same 20 questions,
allowing for the sample size: at least 16/20 when there are no network
errors, and the letter-sequence question must be answered.
