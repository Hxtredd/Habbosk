"""Fingerprints of B3 ask() results for every realistic question (Villa
Brisa main + held-out, Ciudad Coral), each asked by its own user.
tests/data/b3_ask_reference.json was made with the approved B3.5 code
(commit c9fe6b9); test_brain_b4_guards.py checks that later milestones
leave every ask() result unchanged.

Each entry: [status, answered, answer or possible, fingerprint of the whole
result (status, answer, basis, confidence, possible, audience, steps with
their claims, trace)]. Search hits are covered by b2_reference.py.

  python tests/b3_reference.py out.json      (re-create, e.g. from c9fe6b9)
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402


def _flat(r) -> dict:
    return {"status": r.status, "answered": r.answered, "answer": r.answer, "basis": r.basis,
            "confidence": r.confidence, "possible": r.possible, "audience": r.audience,
            "steps": r.steps, "trace": r.trace}


def fingerprints() -> dict:
    out = {}
    d = tempfile.mkdtemp(prefix="sk_b3_ref_")
    try:
        for name, mod, qs in (("brisa", E1, E1.QUESTIONS + E1.HELDOUT), ("coral", E2, E2.QUESTIONS)):
            b, ids, users = mod.build(os.path.join(d, name + ".db"))
            for q, _, _, asker in qs:
                r = b.ask(q, viewer=users[asker])
                blob = json.dumps(_flat(r), ensure_ascii=False, sort_keys=True, default=str)
                out[f"{name}|{asker}|{q}"] = [r.status, r.answered, r.answer or r.possible,
                                              hashlib.sha256(blob.encode()).hexdigest()[:20]]
            b.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(fingerprints(), fh, ensure_ascii=False, indent=0, sort_keys=True)
