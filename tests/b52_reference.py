"""B5.2: the approved references, recomputed on SEPARATED storage.

Each reference dataset is built as before (one development file), migrated
with brain.split.split_file into the library layout, and every question is
asked again by its own person through their own session (their personal
file + the read-only shared file). The results must equal the recorded
references byte for byte:
  B2  tests/data/b2_search_reference.json   (search, FTS5 and plain)
  B3  tests/data/b3_ask_reference.json      (ask)
and B4 / B5.1 answers (category, reason, value, sentence) must equal the
answers of the original single file.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import b2_reference as R2  # noqa: E402
import b3_reference as R3  # noqa: E402
import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402
from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402
from puzzle_assistant.brain.split import split_file, verify_library  # noqa: E402


class Separated:
    """A dataset after migration: one session per person, opened on demand."""

    def __init__(self, build, name: str, root: str):
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir)
        res = build(os.path.join(self.dir, "knowledge.db"))
        self.legacy, self.users = res[0], res[-1]
        self.hk, self.lk = secrets.token_bytes(32), secrets.token_bytes(32)

    def answers(self, fn):
        """fn(brain, viewer) on the ORIGINAL single file (before migrating)."""
        return fn(self.legacy)

    def migrate(self) -> dict:
        self.legacy.close()
        rep = split_file(os.path.join(self.dir, "knowledge.db"), self.dir, self.hk, self.lk)
        verify_library(os.path.join(self.dir, "brain"), self.hk)
        self.lib = Library(os.path.join(self.dir, "brain"), self.hk, self.lk)
        self.uid = {self.lib._user(u["user_id"])["contributor_id"]: u["user_id"]
                    for u in self.lib.users()}
        self._s = {}
        return rep

    def session(self, viewer: str):
        uid = self.uid[viewer]
        if uid not in self._s:
            self._s[uid] = self.lib.open_session(mint_grant(
                self.hk, self.lib.installation_id, self.lib.epoch, uid, "user"))
        return self._s[uid]

    def close(self):
        for s in self._s.values():
            s.close()
        self.lib.close()


def b2_b3_fingerprints(root: str):
    """The same procedure as the recorded references: a fresh dataset for the
    searches only (B2) and another for the asks only (B3)."""
    b2, b3 = {}, {}
    for name, mod, qs in (("brisa", E1, E1.QUESTIONS + E1.HELDOUT), ("coral", E2, E2.QUESTIONS)):
        d = Separated(mod.build, name + "-b2", root)
        d.migrate()
        try:
            for q, _, _, asker in qs:
                s = d.session(d.users[asker])
                for fts in (True, False):
                    blob = json.dumps(R2._flat(s.search(q, use_fts=fts)), ensure_ascii=False)
                    b2[f"{name}|{'fts' if fts else 'plain'}|{asker}|{q}"] = \
                        hashlib.sha256(blob.encode()).hexdigest()[:20]
        finally:
            d.close()
        d = Separated(mod.build, name + "-b3", root)
        d.migrate()
        try:
            for q, _, _, asker in qs:
                r = d.session(d.users[asker]).ask(q)
                blob = json.dumps(R3._flat(r), ensure_ascii=False, sort_keys=True, default=str)
                b3[f"{name}|{asker}|{q}"] = [r.status, r.answered, r.answer or r.possible,
                                             hashlib.sha256(blob.encode()).hexdigest()[:20]]
        finally:
            d.close()
    return b2, b3


def respond_pairs(build, questions, root: str, name: str):
    """(question, asker, before, after): respond() on the single file, then
    on separated storage - (category, reason, value, sentence)."""
    d = Separated(build, name, root)

    def run(fn_viewer):
        return {(q, a): fn_viewer(q, a) for q, a in questions}
    before = run(lambda q, a: _key(d.legacy.respond(q, viewer=d.users[a])))
    d.migrate()
    try:
        after = run(lambda q, a: _key(d.session(d.users[a]).respond(q)))
    finally:
        d.close()
    return [(q, a, before[(q, a)], after[(q, a)]) for q, a in questions]


def _key(r):
    return (r.category, r.reason, r.value, r.sentence)


if __name__ == "__main__":
    root = tempfile.mkdtemp(prefix="sk_b52_ref_")
    try:
        b2, b3 = b2_b3_fingerprints(root)
        with open(os.path.join(HERE, "data", "b2_search_reference.json"), encoding="utf-8") as fh:
            r2 = json.load(fh)
        with open(os.path.join(HERE, "data", "b3_ask_reference.json"), encoding="utf-8") as fh:
            r3 = json.load(fh)
        with open(os.path.join(HERE, "data", "b2_b51_baseline.json"), encoding="utf-8") as fh:
            b51 = json.load(fh)
        print("B2 separated vs the approved B5.1 code:",
              sum(b51["fresh"][k] == b2.get(k) for k in r2), "/", len(r2))
        print("B2 separated vs the older recorded reference:",
              sum(r2[k] == b2.get(k) for k in r2), "/", len(r2), "(the 10 others: B3's intended "
              "improvements)")
        print("B3 on separated storage:", sum(r3[k] == b3.get(k) for k in r3), "/", len(r3))
    finally:
        shutil.rmtree(root, ignore_errors=True)
