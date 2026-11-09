# Windows before/after check of AI changes A-D

| | |
|---|---|
| Code tested | `e9cee60` (branch `claude/kind-dijkstra-usz63w`), AI code only - not the full app |
| Tool | SK AI Speed Check 1.1 `--compare` |
| Run | owner's Windows 11 PC, Python 3.14.10, 2026-11-09 18:52-18:57 local time |
| Model | `gemini-3.5-flash-lite` (unchanged) |
| Method | same 20 questions, v3.3 copy ("before") and e9cee60 ("after") alternating request by request, SK's own time limits; then 5 "question changed" cases each (second question 0.3 s after the first) |
| Raw files | `ai_speed_report_20261109-185701.txt` / `.json` (no key) |

| Comparison | median | p90 | slowest | right | errors |
|---|---|---|---|---|---|
| before: v3.3 | 0.60 s | 0.74 s | 0.86 s | 16/20 | 1 (letter reply discarded) |
| after: A-D | 0.65 s | 0.80 s | 1.17 s | **19/20** | **0** |
| question changed, before (one lane) | 0.98 s | 1.06 s | 1.10 s | 5/5 | 0 |
| question changed, after (newest wins) | **0.63 s** | 0.72 s | 0.73 s | 5/5 | 0 |

- Normal answers: paired per question, after - before = median **+2 ms**
  (after faster in 9 of 20; spread about ±200 ms, one outlier +602 ms on q5).
  No measurable speed difference either way. C works (all "after" requests
  but the first reused the connection) but the saving it removes, the
  connection set-up, is only ~27 ms on this PC: hidden in Google's variation.
- Question changed: the new question was answered **0.35 s sooner** (median),
  in all 5 cases; the 5 old requests were stopped. With a slow or hanging
  old request (baseline: ~1 in 10) the saving would be up to the 6-8 s limit.
- Accuracy: both right 16, only before 0, only after 3. Attributable to D:
  q6 ("¿Qué letra sigue?", reply "V"). q2 and q11 (ORDENAR) flip between
  runs with the same request (baseline showed q2 both ways): model variation.
  Accuracy at least as good as baseline: yes.
- Google was calm during this run: no 503 / hang / rate limit, so overload
  behaviour (B, C reconnect) is verified by the simulated tests only.
- Timing log (A): 30 lines, only whitelisted fields, written in the
  temporary sandbox.
