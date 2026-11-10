"""Summary of the SK trial's AI timing log (trial_data\\diagnostics) for the owner.

Writes trial_results\\TRIAL_SUMMARY_<date-time>.txt and .json in the trial folder.
The timing log holds only numbers and fixed words (no questions, answers or
keys), so the summary can be sent as it is. Standard library only; it reads
only files inside the trial folder.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import statistics
import sys
from collections import Counter
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "trial_data")
SLOW_MS = 2500.0                  # an answer slower than this counts as "slow" (E evidence)


def read_log(data_dir: str = DATA) -> List[dict]:
    rows = []
    for name in ("ai_timing.1.jsonl", "ai_timing.jsonl"):
        p = os.path.join(data_dir, "diagnostics", name)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    return rows


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _times(rows):
    t = [r.get("total_ms", 0.0) for r in rows if r.get("total_ms")]
    if not t:
        return {"n": 0}
    return {"n": len(t), "median_s": round(statistics.median(t) / 1000, 2),
            "p90_s": round(_pct(t, 0.9) / 1000, 2), "max_s": round(max(t) / 1000, 2)}


def summarize(rows: List[dict], data_dir: str = DATA) -> dict:
    asked = [r for r in rows if r.get("outcome") in ("shown", "no_answer", "late")]
    shown = [r for r in rows if r.get("outcome") == "shown"]
    errors = Counter(r.get("error") for r in rows if r.get("outcome") == "no_answer")
    sent = [r for r in rows if r.get("calls")]
    slow = [r for r in shown if r.get("total_ms", 0) > SLOW_MS]
    overload = errors.get("overload", 0) + errors.get("timeout", 0)
    blocked = 0
    try:
        with open(os.path.join(data_dir, "blocked_access.log"), encoding="utf-8") as fh:
            blocked = sum(1 for line in fh if line.strip())
    except OSError:
        pass
    runs = 0
    try:
        with open(os.path.join(data_dir, "trial_runs.log"), encoding="utf-8") as fh:
            runs = sum(1 for line in fh if "  start  " in line)
    except OSError:
        pass
    return {
        "trial_starts": runs,
        "log_lines": len(rows),
        "first": rows[0].get("ts") if rows else None,
        "last": rows[-1].get("ts") if rows else None,
        "outcomes": dict(Counter(r.get("outcome") for r in rows)),
        "no_answer_reasons": dict(errors),
        "answer_time_all": _times(shown),
        "answer_time_express": _times([r for r in shown if r.get("timed")]),
        "answer_time_library": _times([r for r in shown if not r.get("timed")]),
        "waited_for_free_lane_ms_max": round(max([r.get("queue_ms", 0) for r in rows] or [0]), 1),
        "requests_sent": sum(r.get("calls", 0) for r in rows),
        "connection_reused": sum(1 for r in sent if r.get("reused")),
        "reconnected": sum(1 for r in sent if r.get("reconnected")),
        "slow_answers_over_2_5_s": len(slow),
        "overload_or_timeout": overload,
        "e_evidence_share": round((len(slow) + overload) / len(asked), 3) if asked else None,
        "blocked_accesses_to_normal_sk": blocked,
    }


def text(s: dict) -> str:
    def t(x):
        return (f"{x['n']} answers: typical {x['median_s']} s, slowest 10% {x['p90_s']} s, "
                f"slowest {x['max_s']} s") if x.get("n") else "no answers"
    lines = [
        "SK Assistant TRIAL - AI timing summary",
        f"log: {s['log_lines']} entries, {s['first']} to {s['last']}; trial started {s['trial_starts']} time(s)",
        f"outcomes: {s['outcomes']}",
        f"no answer, why: {s['no_answer_reasons'] or 'none'}",
        f"all AI answers: {t(s['answer_time_all'])}",
        f"  Preguntas express: {t(s['answer_time_express'])}",
        f"  library: {t(s['answer_time_library'])}",
        f"longest wait for a free lane: {s['waited_for_free_lane_ms_max']} ms",
        f"requests sent: {s['requests_sent']}; kept connection reused {s['connection_reused']}x; "
        f"reopened {s['reconnected']}x",
        f"slow (> 2.5 s): {s['slow_answers_over_2_5_s']}; overload/timeout: {s['overload_or_timeout']}; "
        f"share of AI questions affected: {s['e_evidence_share']}",
        f"blocked accesses to the normal SK data: {s['blocked_accesses_to_normal_sk']} (should be 0)",
    ]
    return "\n".join(lines)


def main() -> int:
    rows = read_log()
    s = summarize(rows)
    out = os.path.join(ROOT, "trial_results")
    os.makedirs(out, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    pt = os.path.join(out, f"TRIAL_SUMMARY_{stamp}.txt")
    pj = os.path.join(out, f"TRIAL_SUMMARY_{stamp}.json")
    with open(pj, "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False, indent=1)
    with open(pt, "w", encoding="utf-8") as fh:
        fh.write(text(s) + "\n")
    print(text(s))
    print(f"\nSaved:\n  {pt}\n  {pj}\nSend these two files to Claude (no questions, answers or keys in them).")
    return 0


if __name__ == "__main__":
    code = main()
    if sys.platform == "win32":
        try:
            input("\nPress Enter to close... ")
        except EOFError:
            pass
    sys.exit(code)
