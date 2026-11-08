"""Fingerprints of B2 search results for every realistic question (Villa
Brisa main + held-out, Ciudad Coral), each asked by its own user, on the
FTS5 and plain-search paths. tests/data/b2_search_reference.json was made
with the B2 code (commit 8ab56c2); test_brain_reason.py checks that B3
leaves every result unchanged except the listed, intended improvements.

  python tests/b2_reference.py out.json      (re-create, e.g. from B2 code)
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


def _flat(hits):
    return [[h.module, h.record_id, h.score, h.status, h.established, h.text, h.sources,
             json.dumps(h.detail, sort_keys=True, ensure_ascii=False, default=str)] for h in hits]


def fingerprints() -> dict:
    out = {}
    d = tempfile.mkdtemp(prefix="sk_b2_ref_")
    try:
        for name, mod, qs in (("brisa", E1, E1.QUESTIONS + E1.HELDOUT), ("coral", E2, E2.QUESTIONS)):
            b, ids, users = mod.build(os.path.join(d, name + ".db"))
            for q, _, _, asker in qs:
                for fts in (True, False):
                    k = f"{name}|{'fts' if fts else 'plain'}|{asker}|{q}"
                    blob = json.dumps(_flat(b.search(q, viewer=users[asker], use_fts=fts)),
                                      ensure_ascii=False)
                    out[k] = hashlib.sha256(blob.encode()).hexdigest()[:20]
            b.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(fingerprints(), fh, ensure_ascii=False, indent=0, sort_keys=True)
