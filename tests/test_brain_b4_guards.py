"""B4 guarantees around respond():
  * ask() results are identical to the approved B3 reference (c9fe6b9);
  * answering never writes: the database is byte-for-byte unchanged and
    every write method may be disabled without respond() noticing;
  * respond() only reads through calls that apply the asker's visibility;
  * the Brain has no network code, and answers with the network blocked;
  * AI never produces an answer in B4 and cannot verify anything."""
import ast
import dataclasses
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import b3_reference  # noqa: E402
import brain_answer_eval as A  # noqa: E402
import brain_eval as E1  # noqa: E402
import brain_eval_city as E2  # noqa: E402
from puzzle_assistant.brain.store import IMPORTER, SYSTEM, BrainError, KnowledgeStore  # noqa: E402

BRAIN_DIR = os.path.join(ROOT, "puzzle_assistant", "brain")
SETS = (("brisa", E1, [(q, a) for q, _, _, a in E1.QUESTIONS + E1.HELDOUT]),
        ("coral", E2, [(q, a) for q, _, _, a in E2.QUESTIONS]),
        ("nube", A, [(q, a) for q, a, *_ in A.QUESTIONS + A.GENERAL]))
WRITE_METHODS = ("add_claim", "add_source", "add_section", "add_evidence", "add_contributor",
                 "review", "correct", "propose_correction", "propose_share", "approve_share",
                 "link", "_event", "_index_claim", "_set_meta")


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _state(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        statuses = con.execute("SELECT id, status, scope, version FROM claims ORDER BY id").fetchall()
    finally:
        con.close()
    return counts, statuses


def _sweep(b, users, questions, extra_viewers=("", "nobody-at-all")):
    out = []
    for q, asker in questions:
        for v in (users[asker],) + tuple(extra_viewers):
            out.append(b.respond(q, viewer=v))
    return out


class B3Unchanged(unittest.TestCase):
    def test_ask_results_identical_to_the_b3_reference(self):
        with open(os.path.join(HERE, "data", "b3_ask_reference.json"), encoding="utf-8") as fh:
            ref = json.load(fh)
        now = b3_reference.fingerprints()
        self.assertEqual(set(ref), set(now))
        changed = sorted(k for k in ref if ref[k] != now[k])
        self.assertEqual(changed, [])
        self.assertEqual(len(ref), 109)


class AnsweringNeverWrites(unittest.TestCase):
    def test_database_unchanged_after_every_answer(self):
        d = tempfile.mkdtemp(prefix="sk_b4_ro_")
        try:
            for name, mod, qs in SETS:
                path = os.path.join(d, name + ".db")
                b, ids, users = mod.build(path)
                b.search("warm up")                      # caches built before the snapshot
                before = (_sha(path), _state(path), b.store.data_version())
                rs = _sweep(b, users, qs)
                after = (_sha(path), _state(path), b.store.data_version())
                b.close()
                with self.subTest(name=name):
                    self.assertEqual(after, before)
                    self.assertGreater(len(rs), 30)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_every_write_method_can_be_disabled(self):
        d = tempfile.mkdtemp(prefix="sk_b4_nowrite_")
        try:
            b, ids, users = A.build(os.path.join(d, "n.db"))
            patches = [mock.patch.object(KnowledgeStore, m,
                                         side_effect=AssertionError(f"respond() called {m}"))
                       for m in WRITE_METHODS]
            for p in patches:
                p.start()
            try:
                rs = _sweep(b, users, [(q, a) for q, a, *_ in A.QUESTIONS + A.GENERAL])
            finally:
                for p in patches:
                    p.stop()
            self.assertEqual({r.category for r in rs}, {"established", "documented", "unknown"})
            b.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_save_offer_and_no_ai(self):
        d = tempfile.mkdtemp(prefix="sk_b4_ai_")
        try:
            for name, mod, qs in SETS:
                b, ids, users = mod.build(os.path.join(d, name + ".db"))
                for r in _sweep(b, users, qs):
                    self.assertIsNone(r.save_offer)
                    self.assertEqual(r.ai, "none")
                    self.assertNotEqual(r.category, "ai_generated")
                b.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ReadsOnlyThroughCheckedCalls(unittest.TestCase):
    def test_respond_uses_only_viewer_checked_store_calls(self):
        src = open(os.path.join(BRAIN_DIR, "respond.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        store_attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                       and isinstance(n.value, ast.Name) and n.value.id == "store"}
        self.assertEqual(store_attrs, {"cited_evidence", "claim"})
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for forbidden in WRITE_METHODS + ("learn", "evidence", "source", "_claim", "execute",
                                          "executescript", "commit"):
            self.assertNotIn(forbidden, calls, forbidden)
        self.assertNotIn("_db", attrs)


FORBIDDEN_MODULES = ("socket", "ssl", "http", "urllib.request", "urllib3", "requests", "ftplib",
                     "smtplib", "xmlrpc", "asyncio", "subprocess", "telnetlib", "poplib", "imaplib")


class NetworkBoundary(unittest.TestCase):
    def test_no_network_code_in_the_brain(self):
        found = []
        for name in sorted(os.listdir(BRAIN_DIR)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(BRAIN_DIR, name), encoding="utf-8").read())
            for n in ast.walk(tree):
                mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) else []
                for m in mods:
                    if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES):
                        found.append((name, m))
        self.assertEqual(found, [])

    def test_answers_with_the_network_blocked(self):
        def blocked(*a, **k):
            raise AssertionError("network use")
        d = tempfile.mkdtemp(prefix="sk_b4_net_")
        try:
            with mock.patch.object(socket.socket, "connect", blocked), \
                    mock.patch.object(socket, "create_connection", blocked), \
                    mock.patch.object(socket, "getaddrinfo", blocked):
                b, ids, users = A.build(os.path.join(d, "n.db"))
                rs = _sweep(b, users, [(q, a) for q, a, *_ in A.QUESTIONS + A.GENERAL])
                b.close()
            self.assertTrue(rs)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class AICannotChangeKnowledge(unittest.TestCase):
    def test_ai_claims_cannot_be_verified_by_automatic_contributors(self):
        d = tempfile.mkdtemp(prefix="sk_b4_aiclaim_")
        try:
            b, ids, users = A.build(os.path.join(d, "n.db"))
            src = b.add_source("document", title="x")
            cid = b.learn("Ada maintains the Jardín Nube.", "ai", "Jardín Nube")
            b.add_evidence(cid, "direct", src, "Ada maintains the Jardín Nube.")
            for auto in (SYSTEM, IMPORTER):
                with self.assertRaises(BrainError):
                    b.review(cid, "verified", reviewer=auto)
            self.assertEqual(b.claim(cid)["status"], "submitted")
            b.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ResponseShape(unittest.TestCase):
    def test_categories_and_reasons_are_from_the_fixed_lists(self):
        from puzzle_assistant.brain.model import CATEGORIES, UNKNOWN_REASONS
        d = tempfile.mkdtemp(prefix="sk_b4_shape_")
        try:
            for name, mod, qs in SETS:
                b, ids, users = mod.build(os.path.join(d, name + ".db"))
                for r in _sweep(b, users, qs):
                    self.assertIn(r.category, CATEGORIES)
                    if r.category == "unknown":
                        self.assertIn(r.reason, UNKNOWN_REASONS)
                        self.assertIsNone(r.value)
                    else:
                        self.assertEqual(r.reason, "")
                        self.assertIsNotNone(r.value)
                    json.dumps(dataclasses.asdict(r), ensure_ascii=False)   # serialisable
                b.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
