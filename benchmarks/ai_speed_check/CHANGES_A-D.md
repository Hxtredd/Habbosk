# AI speed changes A-D (owner-approved 2026-11-09) - development record

Branch `claude/kind-dijkstra-usz63w` (Session 3, SK Assistant). Not merged,
not released. Baseline: `baseline/BASELINE.md`.

| | Change | Where |
|---|---|---|
| A | Local timing log, numbers and fixed words only, ≤ ~512 KB (2 × 256 KB) | `puzzle_assistant/diagnostics.py`; `Engine.diag` set by `ui.py`; config `ai_timing_log` (default on) |
| B | Newest question wins; ≤ 2 running + ≤ 1 waiting request; stopped requests close their connection; no second call (web search) for a closed question; same question never asked twice | `puzzle_assistant/qa/ai_jobs.py`; `engine.py` (`_drop_ai` everywhere a question is finished) |
| C | Kept-open connection per lane, Gemini host only; reopen once if the server closed it; idle > 50 s not reused; any proxy -> v3.3 urllib method | `puzzle_assistant/qa/transport.py`; default of `AIAnswerer` |
| D | Option text accepted instead of the letter only when unambiguous; first character no longer taken as the letter ("Bambú" ≠ B) | `qa/ai.py` `option_index`, used for CHOICE and ORDER (PAIRS unchanged) |

Unchanged: provider, model choice, request body (prompt, temperature, JSON mode,
token limit, no thinking setting), time limits (6 s express / 8 s library),
cost/quota limits, dependencies, Go to Room.

Small behaviour notes:
- The time under an AI answer is now what the player waited (asked -> shown).
- A request stopped after it reached Google counts once towards the daily
  free-use limit (it may count at Google too); one stopped before sending
  does not.

Log fields (`ai_timing.jsonl`): ts, category (quiz label), kind, provider,
model, outcome (shown / no_answer / late / stale / cancelled), error (fixed
word), status, queue_ms, request_ms, connect_ms, poll_ms, total_ms, calls,
tokens_in, tokens_out, facts, reused, reconnected, searched, timed.

Benchmark: `ai_speed_check.py --compare` (v1.1) runs SK v3.3 (copy) and this
copy's AI code on the same 20 questions, alternating request by request, with
SK's own time limits, plus 5 "question changed" cases each. SK's code runs
with a temporary data folder inside `results\`.
