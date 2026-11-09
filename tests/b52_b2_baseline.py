"""B2 baseline for B5.2, recorded with the APPROVED B5.1 code (not B5.2 code).

    git worktree add /tmp/b51 80fe2745fa78f87b572b00166dddfb1ed05e1eba
    python tests/b52_b2_baseline.py /tmp/b51 tests/data/b2_b51_baseline.json

Runs the recorded B2 procedure (tests/b2_reference.py: build each dataset,
search every question, FTS5 and plain) with the Brain of the given checkout,
twice:
  fresh     built and searched in one process (each dataset in its own process)
  reopened  built, closed, opened again, then searched
and stores both fingerprints for every key. The recorded reference
(b2_search_reference.json) predates B3: the approved code differs from it on
exactly the 10 results of B3's intended improvements (5 questions x FTS5 /
plain, listed in test_brain_reason.B2UnchangedTests.INTENDED) - recorded here
as differs_from_reference. tests/test_brain_b52.py requires B5.2's separated
storage to equal this B5.1 output on all keys.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile


def _fp(flat) -> str:
    return hashlib.sha256(json.dumps(flat, ensure_ascii=False).encode()).hexdigest()[:20]


def one(brain_root: str, name: str, mode: str) -> dict:
    """ONE dataset, ONE mode, in this process (a fresh process each time, like
    the recorded procedure: nothing cached from another dataset or mode)."""
    sys.path.insert(0, os.path.join(brain_root, "tests"))
    sys.path.insert(0, brain_root)
    import b2_reference as R2
    import brain_eval as E1
    import brain_eval_city as E2
    from puzzle_assistant.brain import Brain
    import puzzle_assistant.brain as pb
    assert os.path.abspath(pb.__file__).startswith(os.path.abspath(brain_root)), pb.__file__
    mod, qs = {"brisa": (E1, E1.QUESTIONS + E1.HELDOUT), "coral": (E2, E2.QUESTIONS)}[name]
    d = tempfile.mkdtemp(prefix="sk_b2_b51_")
    out = {}
    try:
        p = os.path.join(d, name + ".db")
        b, ids, users = mod.build(p)
        if mode == "reopened":
            b.close()
            b = Brain(p, "", "")
        for q, _, _, asker in qs:
            for fts in (True, False):
                k = f"{name}|{'fts' if fts else 'plain'}|{asker}|{q}"
                out[k] = R2._flat(b.search(q, viewer=users[asker], use_fts=fts))
        b.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


def run(brain_root: str) -> dict:
    out = {"fresh": {}, "reopened": {}, "lists": {}}
    flat = {}
    for name in ("brisa", "coral"):
        for mode in ("fresh", "reopened"):
            r = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", brain_root,
                                name, mode], capture_output=True, text=True, timeout=900)
            if r.returncode:
                raise RuntimeError(r.stderr[-2000:])
            res = json.loads(r.stdout)
            for k, v in res.items():
                out[mode][k] = _fp(v)
                flat.setdefault(k, {})[mode] = v
    for k, v in flat.items():
        if v["fresh"] != v["reopened"]:
            out["lists"][k] = v
    return out


if __name__ == "__main__":
    if sys.argv[1] == "--one":
        print(json.dumps(one(sys.argv[2], sys.argv[3], sys.argv[4]), ensure_ascii=False))
        sys.exit(0)
    root, dst = sys.argv[1], sys.argv[2]
    data = run(root)
    commit = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                           "b2_search_reference.json"), encoding="utf-8") as fh:
        ref = json.load(fh)
    data["brain_commit"] = commit
    # the recorded reference predates B3: B3's approved, intended improvements change
    # exactly these results (tests/test_brain_reason.py B2UnchangedTests.INTENDED)
    data["differs_from_reference"] = sorted(k for k in ref if data["fresh"][k] != ref[k])
    data["reopened_equals_fresh"] = all(data["fresh"][k] == data["reopened"][k] for k in ref)
    del data["lists"]
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"brain {commit}: differs from the recorded reference on "
          f"{len(data['differs_from_reference'])} of {len(ref)}; reopened == fresh: "
          f"{data['reopened_equals_fresh']}")
