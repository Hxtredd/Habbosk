"""B2 cache performance and memory at several claim counts (temporary
databases only). Run: python tests/brain_memory.py [counts...]"""
from __future__ import annotations

import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import brain_eval_city as E2  # noqa: E402
from brain_speed import _filler  # noqa: E402
from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain import retrieve as R  # noqa: E402

QUESTIONS = ["What is the room ID of the Hotel Coral?", "Who is the caretaker of the Faro del Sur?",
             "¿A dónde lleva la salida norte del Puerto Coral?", "What is the room ID of the Faro?"]


def peak_memory_mb() -> float:
    """Peak memory of this process (Windows: peak working set; else max RSS)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                                                 ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / 1e6
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def ms(f):
    t = time.perf_counter()
    f()
    return (time.perf_counter() - t) * 1000


def _build(n_filler):
    d = tempfile.mkdtemp(prefix="sk_brain_mem_")
    path = os.path.join(d, "k.db")
    b, ids, users = E2.build(path)
    _filler(b, n_filler)
    n = len(b.store.claims(limit=10 ** 9))
    b.close()
    return d, path, ids, users, n


def measure(n_filler: int) -> dict:
    """Timings with memory tracing OFF (tracing slows Python several times),
    then memory in a second pass on a fresh copy."""
    R.VERIFY_FAST = R.EXHAUSTIVE = False
    d, path, ids, users, n = _build(n_filler)
    try:
        b = Brain(path, "", "")
        q = QUESTIONS[0]
        out = {"claims": n, "cold": ms(lambda: b.search(q))}
        warm = []
        for _ in range(5):
            for x in QUESTIONS:
                warm.append(ms(lambda: b.search(x)))
        out["warm_median"], out["warm_max"] = statistics.median(warm), max(warm)
        for key, change in (
                ("after_add", lambda: b.learn("The Hotel Coral has a new lobby.", "user",
                                              "Hotel Coral")),
                ("after_correct", lambda: b.correct(ids["hotel_price"],
                                                    "A room in the Hotel Coral costs 30 coins.")),
                ("after_review", lambda: b.review(ids["suite_id"], "outdated"))):
            runs = []
            for _ in range(3):
                change()
                runs.append(ms(lambda: b.search(q)))
                b.search(q)
            out[key] = statistics.median(runs)
        runs, then = [], []
        for i in range(3):                                 # another program changes the file
            con = sqlite3.connect(path)
            con.execute("UPDATE claims SET updated=? WHERE id=?", (f"x{i}", ids["puerto_id"]))
            con.commit()
            con.close()
            runs.append(ms(lambda: b.search(q)))
            then.append(ms(lambda: b.search(q)))
        out["after_external"], out["then"] = statistics.median(runs), statistics.median(then)
        b.close()
        # memory: fresh open, everything cached for 4 viewers, then 200 more searches
        tracemalloc.start()
        base = tracemalloc.take_snapshot()
        b = Brain(path, "", "")
        for x in QUESTIONS:
            b.search(x)
        for v in ("luis", "marta", "mod"):
            b.search(q, viewer=users[v])
        snap = tracemalloc.take_snapshot()
        out["cache_mb"] = sum(s.size_diff for s in snap.compare_to(base, "filename")) / 1e6
        for i in range(200):
            b.search(QUESTIONS[i % 4])
        snap2 = tracemalloc.take_snapshot()
        out["growth_200_searches_mb"] = sum(s.size_diff for s in snap2.compare_to(snap, "filename")) / 1e6
        tracemalloc.stop()
        b.close()
        out["peak_rss_mb"] = peak_memory_mb()
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def describe(r: dict) -> str:
    return (f"{r['claims']:6d} claims | first search after opening {r['cold']:6.1f} ms | later: median "
              f"{r['warm_median']:5.1f}, max {r['warm_max']:5.1f} ms\n"
              f"             after add {r['after_add']:5.1f} | correct {r['after_correct']:5.1f} | "
              f"review {r['after_review']:5.1f} | external change {r['after_external']:6.1f} "
              f"(then {r['then']:5.1f}) ms\n"
              f"             cache + names (4 viewers) {r['cache_mb']:5.1f} MB (tracemalloc) | "
              f"growth over 200 more searches {r['growth_200_searches_mb']:+.2f} MB | "
              f"process peak {r['peak_rss_mb']:.0f} MB")


if __name__ == "__main__":
    for c in [int(x) for x in sys.argv[1:]] or [1000, 10000, 50000]:
        print(describe(measure(c)), flush=True)
