"""B2 search latency benchmark (synthetic, temporary databases only).

Scenarios (each with 10,000 filler claims, as in the 10,005-claim speed test):
  speed     the original speed test: Demo Garden claims + filler, 3 queries
  brisa     Villa Brisa (SK-BRAIN-EVAL-001) + filler, all main + held-out questions
  coral     Ciudad Coral (SK-BRAIN-EVAL-002, 4 users) + filler, all questions, each
            asked by its own user
Each query is timed (median of 5 runs after one warm-up; the slowest single run is
reported separately as jitter), on the FTS5 path
and on the plain-search fallback. Reports worst case, average and p95.

  python tests/brain_speed.py            report
  python tests/brain_speed.py --snapshot out.json
                                         also store every result (order, score,
                                         status, why) to prove an optimisation
                                         changed nothing
"""
from __future__ import annotations

import json
import os
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402
from puzzle_assistant.brain import Brain  # noqa: E402

FILLER_WORDS = ["garden", "fountain", "archway", "courtyard", "plaza", "shop", "bank", "casino"]


def _filler(b, n=10000):
    with b.store._lock:
        for i in range(n):
            b.store.add_claim(f"The {FILLER_WORDS[i % 8]} number {i} is in area {i % 97}.", "user",
                              subject=f"{FILLER_WORDS[i % 8]} {i}")


def scenarios(root):
    import test_brain_retrieval as T
    b = Brain(os.path.join(root, "speed.db"), "", "")
    _filler(b)
    T.load_original(b, True)
    yield "speed", b, [(T.QUESTIONS[3], None), (T.QUESTIONS[8], None), (T.QUESTIONS[1], None)]
    b.close()
    b, ids, users = E1.build(os.path.join(root, "brisa.db"))
    _filler(b)
    yield "brisa", b, [(q, users[a]) for q, _, _, a in E1.QUESTIONS + E1.HELDOUT]
    b.close()
    b, ids, users = E2.build(os.path.join(root, "coral.db"))
    _filler(b)
    yield "coral", b, [(q, users[a]) for q, _, _, a in E2.QUESTIONS]
    b.close()


def _p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def run(snapshot=None):
    root = tempfile.mkdtemp(prefix="sk_brain_speed_")
    times = {}            # (scenario, path) -> [ms]
    worst = {}
    jitter = {}
    snap = {}
    try:
        for name, b, queries in scenarios(root):
            b.search("warm up")
            for fts in (True, False):
                key = (name, "FTS5" if fts else "fallback")
                times[key] = []
                for i, (q, viewer) in enumerate(queries):
                    b.search(q, viewer=viewer, use_fts=fts)            # warm-up (caches)
                    runs = []
                    for _ in range(5):
                        t = time.perf_counter()
                        hits = b.search(q, viewer=viewer, use_fts=fts)
                        runs.append((time.perf_counter() - t) * 1000)
                    ms = statistics.median(runs)
                    times[key].append(ms)
                    jitter[key] = max(jitter.get(key, 0.0), max(runs))
                    if ms > worst.get(key, (0, ""))[0]:
                        worst[key] = (ms, q)
                    if snapshot is not None:
                        snap[f"{name}|{key[1]}|{i}|{q}"] = [
                            [h.module, h.record_id, h.score, h.status, h.established,
                             json.dumps(h.detail.get("why"), sort_keys=True, ensure_ascii=False)]
                            for h in hits]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    lines = ["[B2 search latency, 10,000+ claims per scenario; goal 50 ms]",
             f"  {'scenario':8} {'path':9} {'queries':>7} {'avg':>7} {'p95':>7} {'worst':>7} "
             f"{'jitter':>7}"]
    allv = {"FTS5": [], "fallback": []}
    allj = {"FTS5": 0.0, "fallback": 0.0}
    for (name, path), xs in times.items():
        allv[path] += xs
        allj[path] = max(allj[path], jitter[(name, path)])
        lines.append(f"  {name:8} {path:9} {len(xs):7d} {statistics.mean(xs):6.1f}  "
                     f"{_p95(xs):6.1f}  {max(xs):6.1f}  {jitter[(name, path)]:6.1f}   "
                     f"worst: {worst[(name, path)][1][:44]!r}")
    for path, xs in allv.items():
        lines.append(f"  {'ALL':8} {path:9} {len(xs):7d} {statistics.mean(xs):6.1f}  "
                     f"{_p95(xs):6.1f}  {max(xs):6.1f}  {allj[path]:6.1f}")
    if snapshot is not None:
        with open(snapshot, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=0, sort_keys=True)
    return "\n".join(lines), times, allj


if __name__ == "__main__":
    out = None
    if "--snapshot" in sys.argv:
        out = sys.argv[sys.argv.index("--snapshot") + 1]
    print(run(out)[0])
