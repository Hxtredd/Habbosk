"""Deliberate-break check for the v3.4 AI changes A-D (development only, not shipped).

Each break changes one safety or behaviour rule in a throw-away copy of the
code; the tests must then FAIL ("caught"). An unbroken copy must PASS (control).

    python tools/deliberate_breaks_v34.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = ["tests.test_ai_speed_v34", "tests.test_ai", "tests.test_express", "tests.test_question_marks",
         "tests.test_marks_after_puzzle"]

# (id, what it breaks, file, old text, new text)
BREAKS = [
    ("B1", "B: a new question does not stop requests for other questions", "puzzle_assistant/qa/ai_jobs.py",
     "                if j.ident != ident:", "                if False:"),
    ("B2", "B: only one lane (v3.3 behaviour)", "puzzle_assistant/qa/ai_jobs.py",
     "LANES = 2", "LANES = 1"),
    ("B3", "B: the same question can be asked twice", "puzzle_assistant/qa/ai_jobs.py",
     "            if same:                            # already asked: never twice", "            if False:"),
    ("B4", "B: waiting requests are not removed from the queue", "puzzle_assistant/qa/ai_jobs.py",
     "        if job.future.cancel():                 # had not started: never sent",
     "        if False:"),
    ("B5", "B: the engine does not stop the request when a question is finished", "puzzle_assistant/engine.py",
     "        self._ai_jobs.cancel(j)", "        pass"),
    ("B6", "B: the engine keeps the request when the question window closes", "puzzle_assistant/engine.py",
     "                        self._drop_ai(qstate)          # v3.4: the question is gone", "                        pass"),
    ("B7", "B: no step-wise wait (Windows: a stopped request keeps waiting)", "puzzle_assistant/qa/transport.py",
     "                wait_for_reply(conn, token, timeout)\n", ""),
    ("B8", "B: the step-wise wait ignores cancellation", "puzzle_assistant/qa/transport.py",
     "    while True:\n        if token.cancelled:\n            raise Aborted(\"request cancelled\", sent=True)\n",
     "    while True:\n"),
    ("B9", "B: second AI call after a web search for a closed question", "puzzle_assistant/qa/ai.py",
     "snippets = [] if token is not None and token.cancelled else self._search(q)", "snippets = self._search(q)"),
    ("B10", "B: a stopped request that was sent is not counted", "puzzle_assistant/qa/ai.py",
     "            if exc.sent:                        # it reached the provider: count it", "            if False:"),
    ("C1", "C: a closed kept-open connection is not reopened", "puzzle_assistant/qa/transport.py",
     "                if reused and attempt == 0:", "                if False:"),
    ("C2", "C: a failed fresh connection is retried too", "puzzle_assistant/qa/transport.py",
     "                if reused and attempt == 0:", "                if attempt == 0:"),
    ("C3", "C: kept-open connection although a proxy is configured", "puzzle_assistant/qa/transport.py",
     " or host not in KEEPALIVE_HOSTS or self.proxy_configured():", " or host not in KEEPALIVE_HOSTS:"),
    ("C4", "C: kept-open connection to every host", "puzzle_assistant/qa/transport.py",
     "parts.scheme != \"https\" or host not in KEEPALIVE_HOSTS or", "parts.scheme != \"https\" or"),
    ("C5", "C: idle connections never expire", "puzzle_assistant/qa/transport.py",
     "MAX_IDLE = 50.0", "MAX_IDLE = 1e9"),
    ("A1", "A: the timing log accepts any field (could hold text)", "puzzle_assistant/diagnostics.py",
     "        if spec is None or value is None:\n            continue\n",
     "        if value is None:\n            continue\n        if spec is None:\n            out[name] = value\n            continue\n"),
    ("A2", "A: the timing log has no size limit", "puzzle_assistant/diagnostics.py",
     "if size and size + len(", "if False and size and size + len("),
    ("A3", "A: the shown time leaves out the waiting", "puzzle_assistant/engine.py",
     "waited = (time.monotonic() - j.submitted) if j is not None else res.ms / 1000", "waited = res.ms / 1000"),
    ("A4", "A: counting can fail an answer when SK shuts down", "puzzle_assistant/qa/ai.py",
     "        except Exception:\n            # v3.4: the memory may already be closed",
     "        except ZeroDivisionError:\n            # v3.4: the memory may already be closed"),
    ("D1", "D: a letter that is also another option's text is accepted", "puzzle_assistant/qa/ai.py",
     "        if text_hits and text_hits != [letter_idx]:", "        if False:"),
    ("D2", "D: letter and text naming different options is accepted", "puzzle_assistant/qa/ai.py",
     "            if rest_hits and letter_idx not in rest_hits:", "            if False:"),
    ("D3", "D: the option's text is not accepted (v3.3 behaviour)", "puzzle_assistant/qa/ai.py",
     "    if len(text_hits) == 1:", "    if False:"),
]

COPY = ["puzzle_assistant", "tests", "assets", "atlas_input", "tessdata", "tools"]


def make_copy(base: str) -> str:
    root = os.path.join(base, "sk")
    os.makedirs(root)
    for d in COPY:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(root, d),
                        ignore=shutil.ignore_patterns("__pycache__"))
    return root


def run_tests(root: str) -> bool:
    data = tempfile.mkdtemp(prefix="sk_breaks_data_")
    env = dict(os.environ, SK_ASSISTANT_DATA=data, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", *TESTS], cwd=root, env=env,
                       capture_output=True, text=True, timeout=600)
    shutil.rmtree(data, ignore_errors=True)
    return r.returncode == 0


def main() -> int:
    base = tempfile.mkdtemp(prefix="sk_breaks_")
    root = make_copy(base)
    t0 = time.time()
    control = run_tests(root)
    print(f"control (no break): {'PASS' if control else 'FAIL'}")
    results = []
    for bid, what, rel, old, new in BREAKS:
        path = os.path.join(root, rel)
        with open(path, encoding="utf-8") as fh:
            orig = fh.read()
        if orig.count(old) != 1:
            print(f"{bid}: break text not found exactly once in {rel} - NOT APPLIED")
            results.append((bid, what, "not applied"))
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(orig.replace(old, new))
        try:
            caught = not run_tests(root)
        finally:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(orig)
        results.append((bid, what, "caught" if caught else "MISSED"))
        print(f"{bid:4s} {'caught' if caught else 'MISSED'}  {what}")
    shutil.rmtree(base, ignore_errors=True)
    n = sum(1 for r in results if r[2] == "caught")
    print(f"\n{n} of {len(BREAKS)} breaks caught; control {'passed' if control else 'FAILED'}; "
          f"{time.time() - t0:.0f} s")
    return 0 if control and n == len(BREAKS) else 1


if __name__ == "__main__":
    sys.exit(main())
