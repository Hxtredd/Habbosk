"""B5.2 storage separation: personal and shared knowledge in separate files.

Synthetic data only. Covers: B2-B5.1 behaviour on separated storage, privacy
markers in every kind of row (raw byte scans of every file and sidecar),
sessions and signed grants, proposals and moderator verification, ids and
registry restores, keyed tags, withdrawal (with power loss at every step and
secure deletion), migration (with failures and power loss at every step),
manifests and recovery, concurrency and boundaries."""
import ast
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import b52_reference as REF  # noqa: E402
import b52_world as W  # noqa: E402
import brain_answer_eval as A  # noqa: E402
import brain_understanding_eval as U  # noqa: E402
from puzzle_assistant.brain import split as S  # noqa: E402
from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402
from puzzle_assistant.brain.store import BrainError  # noqa: E402

BRAIN_DIR = os.path.join(ROOT, "puzzle_assistant", "brain")


def _sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _child(*args, crash=""):
    env = dict(os.environ, SK_TEST_CRASH_AT=crash)
    return subprocess.run([sys.executable, os.path.join(HERE, "b52_child.py")] + list(args),
                          env=env, capture_output=True, text=True, timeout=300)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_b52_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


# -- 1. B2-B5.1 behaviour on separated storage -----------------------------------------------
class ReferencesOnSeparatedStorage(Tmp):
    def test_b3_reference_and_b2_identical_to_the_approved_b51_code(self):
        b2, b3 = REF.b2_b3_fingerprints(self.d)
        with open(os.path.join(HERE, "data", "b3_ask_reference.json"), encoding="utf-8") as fh:
            r3 = json.load(fh)
        with open(os.path.join(HERE, "data", "b2_search_reference.json"), encoding="utf-8") as fh:
            r2 = json.load(fh)
        with open(os.path.join(HERE, "data", "b2_b51_baseline.json"), encoding="utf-8") as fh:
            b51 = json.load(fh)              # recorded with the B5.1 code (b52_b2_baseline.py)
        self.assertEqual(sorted(k for k in r3 if r3[k] != b3.get(k)), [])
        self.assertEqual(len(r3), 109)
        # B2: every result identical to the approved B5.1 code (80fe274), fresh AND reopened
        self.assertEqual(b51["brain_commit"], "80fe2745fa78f87b572b00166dddfb1ed05e1eba")
        self.assertEqual(set(b51["fresh"]), set(r2))
        self.assertEqual(sorted(k for k in r2 if b2.get(k) != b51["fresh"][k]), [])
        self.assertTrue(b51["reopened_equals_fresh"])
        # against the older recorded reference: exactly B3's 10 intended improvements
        import test_brain_reason as TR
        diff = sorted(k for k in r2 if r2[k] != b2.get(k))
        self.assertEqual(diff, b51["differs_from_reference"])
        self.assertEqual({k.split("|", 3)[3] for k in diff}, TR.B2UnchangedTests.INTENDED)
        self.assertEqual(len(diff), 10)

    def test_b4_answers_identical(self):
        pairs = REF.respond_pairs(A.build, [(q, a) for q, a, *_ in A.QUESTIONS + A.GENERAL],
                                  self.d, "nube")
        self.assertEqual([p for p in pairs if p[2] != p[3]], [])
        self.assertEqual(len(pairs), 45)

    def test_b51_answers_identical(self):
        qs = [(q, "ana") for t in U.TARGETS for q, _ in U.variants(t)[::6]] + \
             [(q, a) for q, a, _ in U.PROBES + U.HELDOUT]
        pairs = REF.respond_pairs(lambda p: U.build(p), qs, self.d, "isla")
        self.assertEqual([p for p in pairs if p[2] != p[3]], [])
        self.assertGreater(len(pairs), 350)


# -- 2. separation: raw byte scans of every file --------------------------------------------------
class Separation(Tmp):
    def setUp(self):
        super().setUp()
        self.w = W.World(self.d).build()

    def test_markers_only_in_their_owners_files(self):
        for who, markers in W.PERSONAL.items():
            own = self.w.owner_files(who)
            for m in markers:
                hits = self.w.where(m)
                self.assertTrue(hits, m)                     # the scan does find it
                self.assertEqual({os.path.relpath(p, self.d) for p in hits - own}, set(), (who, m))

    def test_unshared_and_withheld_material_not_in_shared(self):
        shared = {p for p in self.w.files() if os.path.basename(p).startswith("shared.db")}
        for m in ["ANA-UNSHARED-TITLE-MARK", W.PLAIN_HASH, "plainhash-luis-0001",
                  "ANA-PRIVATE-NOTES-MARK", "ANA-REVIEW-NOTE-MARK"]:
            self.assertEqual(self.w.where(m) & shared, set(), m)

    def test_granted_material_reaches_shared_and_is_cited(self):
        self.assertTrue(any(p.endswith("shared.db") for p in self.w.where("ANA-TITLE-MARK")))
        s = self.w.open("luis")
        r = s.respond("Who maintains the Lantern Garden?")
        self.assertEqual((r.category, r.value), ("established", "Mira"))
        self.assertEqual(r.citations[0].evidence[0]["source"], "ANA-TITLE-MARK")
        s.close()

    def test_nobody_sees_anyone_elses_personal_knowledge(self):
        for who in ("luis", "eve", "kim"):
            s = self.w.open(who)
            for q in ("Who maintains the Secret Cellar?", "Secret Celar caretaker"):
                r = s.respond(q)
                blob = repr((r.sentence, r.value, r.clarification, r.related, r.steps))
                self.assertNotIn("Zeta", blob)
                self.assertNotIn("Secret Cellar", blob.replace(q, ""))
            s.close()
        s = self.w.open("ana")
        self.assertEqual(s.respond("Who maintains the Secret Cellar?").value, "Zeta")
        self.assertIsNone(s.respond("What is the room ID of the Hidden Vault?").value)
        s.close()

    def test_a_session_opens_only_its_own_file_and_shared_read_only(self):
        s = self.w.open("ana")
        dbs = {r[1]: r[2] for r in s.store._db.execute("PRAGMA database_list")}
        self.assertEqual(set(dbs) - {"temp"}, {"main", "shared"})
        self.assertEqual(os.path.basename(dbs["main"]),
                         self.w.lib._user(self.w.ids["ana"])["file_id"] + ".db")
        self.assertTrue(dbs["shared"].endswith("shared.db"))
        with self.assertRaises(sqlite3.OperationalError):
            s.store._db.execute("UPDATE shared.claims SET text='x'")
        s.close()

    def test_a_viewer_naming_someone_else_is_refused(self):
        s = self.w.open("ana")
        other = self.w.lib._user(self.w.ids["luis"])["contributor_id"]
        for call in (lambda: s.respond("x", viewer=other), lambda: s.learn("x", by=other),
                     lambda: s.claim(1, viewer=other), lambda: s.store.claims(viewer=other)):
            with self.assertRaises(BrainError):
                call()
        s.close()

    def test_shared_file_holds_no_personal_rows(self):
        con = sqlite3.connect(self.w.lib.path("shared.db"))
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM claims WHERE scope!='shared'"
                                         ).fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sources WHERE private=1"
                                         ).fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM meta WHERE key IN ('local_user', "
                                         "'owner')").fetchone()[0], 0)
            names = {r[0] for r in con.execute("SELECT display_name FROM contributors WHERE "
                                               "kind NOT IN ('system','importer','moderator')")}
            self.assertEqual(names, {"contributor"})
        finally:
            con.close()

    def test_registry_holds_no_names_but_moderators(self):
        con = sqlite3.connect(self.w.lib.path("registry.db"))
        try:
            rows = con.execute("SELECT kind, display_name FROM users").fetchall()
        finally:
            con.close()
        self.assertTrue(all(n == "" for k, n in rows if k != "moderator"))


# -- 3. sessions and grants -------------------------------------------------------------------------
class Grants(Tmp):
    def setUp(self):
        super().setUp()
        self.w = W.World(self.d)
        self.uid = self.w.lib.create_user("Ana")
        self.mod = self.w.lib.create_user("Mod", "moderator")
        self.lib = self.w.lib

    def refused(self, grant, now=None):
        with self.assertRaises(BrainError):
            self.lib.open_session(grant, now)

    def test_each_failure_is_refused(self):
        L = self.lib
        g = mint_grant(self.w.hk, L.installation_id, L.epoch, self.uid, "user")
        self.refused(g[:-3] + ("AAA" if not g.endswith("AAA") else "BBB"))   # bad signature
        self.refused(mint_grant(secrets.token_bytes(32), L.installation_id, L.epoch, self.uid,
                                "user"))                                        # other key
        self.refused(mint_grant(self.w.hk, "other", L.epoch, self.uid, "user"))
        self.refused(mint_grant(self.w.hk, L.installation_id, L.epoch, "nobody", "user"))
        self.refused(mint_grant(self.w.hk, L.installation_id, L.epoch, self.uid, "moderator"))
        self.refused(mint_grant(self.w.hk, L.installation_id, L.epoch + 1, self.uid, "user"))
        old = mint_grant(self.w.hk, L.installation_id, L.epoch, self.uid, "user",
                         now=time.time() - 200)
        self.refused(old)                                                       # expired
        future = mint_grant(self.w.hk, L.installation_id, L.epoch, self.uid, "user",
                            now=time.time() + 120)
        self.refused(future)                                                    # clock skew
        self.refused("not-a-grant")
        ok = mint_grant(self.w.hk, L.installation_id, L.epoch, self.uid, "user",
                        now=time.time() - 20)
        self.lib.open_session(ok).close()                                      # within skew
        self.refused(ok)                                                        # replay

    def test_ttl_is_capped(self):
        g = mint_grant(self.w.hk, self.lib.installation_id, self.lib.epoch, self.uid, "user",
                       ttl=3600, now=time.time() - 120)
        self.refused(g)

    def test_registry_restore_raises_the_epoch(self):
        L = self.lib
        g = mint_grant(self.w.hk, L.installation_id, L.epoch, self.uid, "user")
        L.close()
        reg = os.path.join(self.d, "brain", "registry.db")
        snap = os.path.join(self.d, "snap.db")
        shutil.copyfile(reg, snap)
        with open(os.path.join(self.d, "brain", "epoch.json"), "w") as fh:
            json.dump({"epoch": 5}, fh)                 # the file says a later epoch was used
        L2 = Library(os.path.join(self.d, "brain"), self.w.hk, self.w.lk)
        self.assertEqual(L2.epoch, 6)
        with self.assertRaises(BrainError):
            L2.open_session(g)                          # made before the restore
        L2.open_session(mint_grant(self.w.hk, L2.installation_id, L2.epoch, self.uid,
                                   "user")).close()
        L2.close()

    def test_keys_never_in_any_file(self):
        self.w.ids = {"ana": self.uid, "mod": self.mod}
        s = self.w.open("ana")
        s.learn("Mira maintains the Lantern Garden.", "user")
        s.close()
        for k in (self.w.hk, self.w.lk):
            for form in (k, k.hex().encode(), k.hex().upper().encode()):
                for p in self.w.files():
                    with open(p, "rb") as fh:
                        self.assertNotIn(form, fh.read(), p)

    def test_a_session_cannot_be_saved(self):
        import pickle
        self.w.ids = {"ana": self.uid}
        s = self.w.open("ana")
        with self.assertRaises(TypeError):
            pickle.dumps(s)
        s.close()


# -- 4. proposals and verification --------------------------------------------------------------------
class Proposals(Tmp):
    def setUp(self):
        super().setUp()
        self.w = W.World(self.d).build()

    def test_owner_verification_never_carries_over(self):
        s = self.w.open("ana")
        self.assertEqual(s.claim(self.w.ana_claim)["status"], "verified")
        src = s.add_source("document", title="t2", content_hash="h2")
        c = s.learn("Nora maintains the Clock Tower.", "imported", "Clock Tower")
        s.add_evidence(c, "direct", src, "Nora maintains the Clock Tower.")
        s.review(c, "verified")
        tok = s.propose(c, {src: ["quote"]})
        s.close()
        m = self.w.open("mod", "moderator")
        p = [x for x in m.proposals() if x["token"] == tok][0]
        cid = m.approve(p["file"], tok)
        self.assertEqual(m.store.claim(cid, m._me_id)["status"], "awaiting_verification")
        self.assertEqual(m.approve(p["file"], tok), cid)                  # repeating is harmless
        m.close()
        s = self.w.open("luis")
        r = s.respond("Who maintains the Clock Tower?")
        self.assertNotEqual(r.category, "established")
        s.close()

    def test_stub_only_evidence_cannot_be_verified(self):
        s = self.w.open("ana")
        src = s.add_source("document", title="t3", content_hash="h3")
        c = s.learn("Nora maintains the Old Pier.", "imported", "Old Pier")
        s.add_evidence(c, "direct", src, "Nora maintains the Old Pier.")
        tok = s.propose(c, {src: []})                                    # stub only
        s.close()
        m = self.w.open("mod", "moderator")
        p = [x for x in m.proposals() if x["token"] == tok][0]
        self.assertIsNone(p["material"][0]["quote"])
        cid = m.approve(p["file"], tok)
        with self.assertRaises(BrainError):
            m.review(cid, "verified")
        m.close()

    def test_outbox_holds_only_the_selection(self):
        s = self.w.open("ana")
        fid = self.w.lib._user(self.w.ids["ana"])["file_id"]
        s.close()
        con = sqlite3.connect(self.w.lib.path("outbox", fid + ".db"))
        try:
            props = con.execute("SELECT text FROM proposals").fetchall()
            mats = con.execute("SELECT quote, title, full_text FROM material").fetchall()
        finally:
            con.close()
        self.assertEqual(props, [(W.GRANTED_QUOTE,)])
        self.assertEqual(mats, [(W.GRANTED_QUOTE, "ANA-TITLE-MARK", "ANA-FULLTEXT-MARK")])
        for m in W.PERSONAL["ana"]:
            self.assertFalse(any(p.endswith(fid + ".db") and "outbox" in p
                                 for p in self.w.where(m)), m)

    def test_private_sources_and_others_claims_cannot_be_proposed(self):
        s = self.w.open("ana")
        priv = s.add_source("document", title="p", private=True)
        c = s.learn("Lio maintains the Rope Shed.", "user")
        s.add_evidence(c, "direct", priv, "Lio maintains the Rope Shed.")
        with self.assertRaises(BrainError):
            s.propose(c, {priv: ["quote"]})
        with self.assertRaises(BrainError):
            s.propose(self.w.shared_claim)                               # not a personal claim
        s.close()

    def test_moderators_read_outboxes_read_only_and_users_never(self):
        m = self.w.open("mod", "moderator")
        fid = self.w.lib._user(self.w.ids["ana"])["file_id"]
        con = self.w.lib._outbox(fid, write=False)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("DELETE FROM proposals")
        con.close()
        m.close()
        s = self.w.open("luis")
        self.assertFalse(hasattr(s, "proposals"))
        s.close()

    def test_withdraw_proposal_and_decisions(self):
        s = self.w.open("ana")
        self.assertEqual(s.decisions()[self.w.ana_token]["decision"], "approved")
        c = s.learn("Lio maintains the Rope Shed.", "user")
        tok = s.propose(c, {})
        s.withdraw_proposal(tok)
        s.close()
        m = self.w.open("mod", "moderator")
        self.assertNotIn(tok, [p["token"] for p in m.proposals()])
        m.close()


# -- 5. ids and registry restores -------------------------------------------------------------------
class Ids(Tmp):
    def test_unique_across_files_even_after_a_registry_restore(self):
        w = W.World(self.d).build()
        reg = w.lib.path("registry.db")
        w.lib.close()
        old = os.path.join(self.d, "registry-old.db")
        shutil.copyfile(reg, old)
        w.lib = Library(os.path.join(self.d, "brain"), w.hk, w.lk)
        s = w.open("luis")
        for n in range(80):                               # more ids in Luis's file
            s.learn(f"Note {n} about the Fish Market.", "user")
        s.close()
        w.lib.close()
        for suffix in ("", "-wal", "-shm"):               # registry restored from the old copy
            if os.path.exists(reg + suffix):
                os.remove(reg + suffix)
        shutil.copyfile(old, reg)
        w.lib = Library(os.path.join(self.d, "brain"), w.hk, w.lk)
        a = w.open("ana")
        c = a.learn("Bruno maintains the Fish Market.", "user")
        tok = a.propose(c, {})
        a.close()
        m = w.open("mod", "moderator")
        p = [x for x in m.proposals() if x["token"] == tok][0]
        m.approve(p["file"], tok)                         # new shared ids
        m.close()
        s = w.open("luis")                                # no collision: the session opens
        self.assertEqual(s.store.id_collisions(), [])
        s.close()

    def test_a_forced_collision_is_refused_and_nothing_changes(self):
        w = W.World(self.d).build()
        s = w.open("luis")
        mine = s.learn("Lio maintains the Rope Shed.", "user")
        path = s.store.path
        s.close()
        con = sqlite3.connect(w.lib.path("shared.db"))
        con.execute("INSERT INTO claims (id, text, origin, status, scope, owner_id, version, "
                    "created, created_by, updated) VALUES (?, 'x', 'user', 'verified', 'shared', "
                    "'system', 1, '', 'system', '')", (mine,))
        con.commit()
        con.close()
        before = _sha(path)
        with self.assertRaises(BrainError) as cm:
            w.open("luis")
        self.assertIn("collide", str(cm.exception))
        self.assertEqual(_sha(path), before)

    def test_concurrent_allocation_from_two_libraries(self):
        w = W.World(self.d)
        L2 = Library(os.path.join(self.d, "brain"), w.hk, w.lk)
        got = [[], []]

        def run(lib, out):
            a = lib.allocator()
            for _ in range(300):
                out.append(a("claims"))
        t = [threading.Thread(target=run, args=(L, o)) for L, o in ((w.lib, got[0]),
                                                                     (L2, got[1]))]
        for x in t:
            x.start()
        for x in t:
            x.join()
        self.assertEqual(len(set(got[0]) | set(got[1])), 600)
        L2.close()


# -- 6. keyed tags (never plain document hashes in shared storage) ---------------------------------------
class Tags(Tmp):
    def test_same_material_counts_once_and_no_plain_hash(self):
        w = W.World(self.d).build()
        s = w.open("luis")
        src = s.add_source("document", title="L", content_hash=W.PLAIN_HASH)  # same material as Ana's
        c = s.learn("Mira maintains the Lantern Garden.", "imported", "Lantern Garden")
        s.add_evidence(c, "confirmation", src, "Mira maintains the Lantern Garden.")
        tok = s.propose(c, {src: ["quote"]})
        s.close()
        m = w.open("mod", "moderator")
        p = [x for x in m.proposals() if x["token"] == tok][0]
        cid = m.approve(p["file"], tok)
        tags = {r[0] for r in m.store._db.execute("SELECT content_hash FROM sources")}
        self.assertNotIn(W.PLAIN_HASH, tags)
        self.assertEqual(len({w.lib.tag(W.PLAIN_HASH)} & tags), 1)
        m.close()
        self.assertFalse(any(p.endswith("shared.db") for p in w.where(W.PLAIN_HASH)))
        other = Library(os.path.join(self.d, "other"), w.hk, secrets.token_bytes(32))
        self.assertNotEqual(other.tag(W.PLAIN_HASH), w.lib.tag(W.PLAIN_HASH))
        other.close()
        self.assertIsInstance(cid, int)


# -- 7. withdrawal ----------------------------------------------------------------------------------------
class Withdrawal(Tmp):
    def setUp(self):
        super().setUp()
        self.w = W.World(self.d).build()

    def gone(self):
        """Ana's granted material: absent from shared.db (+ sidecars), the outbox,
        every other person's files; still complete in Ana's personal file."""
        own_personal = self.w.personal_files("ana")
        for m in W.GRANTED[:3]:
            self.assertEqual({os.path.relpath(p, self.d) for p in self.w.where(m) - own_personal},
                             set(), m)
            self.assertTrue(self.w.where(m) & own_personal, m)

    def test_withdrawal_removes_material_everywhere_but_the_owner(self):
        s = self.w.open("ana")
        self.assertEqual(s.withdraw_source(self.w.ana_src), "complete")
        self.assertEqual(s.respond("Who maintains the Lantern Garden?").value, "Mira")  # own claim
        s.close()
        self.gone()
        for p in self.w.files():
            self.assertFalse(p.endswith("-journal"), p)
        l = self.w.open("luis")
        r = l.respond("Who maintains the Lantern Garden?")
        self.assertEqual((r.category, r.reason, r.possible), ("unknown", "evidence_withdrawn",
                                                              "Mira"))
        self.assertIn("withdrawn by its owner", r.sentence)
        self.assertEqual(r.citations[0].evidence if r.citations else [], [])
        l.close()
        m = self.w.open("mod", "moderator")
        self.assertEqual([x["id"] for x in m.review_list()], [self.w.shared_claim])
        raw = m.store._db.execute("SELECT status FROM claims WHERE id=?",
                                  (self.w.shared_claim,)).fetchone()[0]
        self.assertEqual(raw, "verified")                 # stored status unchanged: review it
        m.close()

    def test_granting_again_does_not_restore_support(self):
        s = self.w.open("ana")
        s.withdraw_source(self.w.ana_src)
        with self.assertRaises(BrainError):
            s.propose(self.w.ana_claim, {self.w.ana_src: ["quote"]})
        s.close()

    def test_a_reader_holding_a_snapshot_keeps_the_purge_pending(self):
        reader = sqlite3.connect(self.w.lib.path("shared.db"))
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM claims").fetchone()          # an open snapshot
        s = self.w.open("ana")
        with mock.patch("puzzle_assistant.brain.library.checkpoint",
                        side_effect=lambda con, tries=50, wait=0.05: False):
            self.assertEqual(s.withdraw_source(self.w.ana_src), "purge_pending")
        l = self.w.open("luis")
        self.assertEqual(l.respond("Who maintains the Lantern Garden?").reason, "evidence_withdrawn")
        l.close()
        reader.rollback()
        reader.close()
        self.assertEqual(s.finish_pending(), "complete")
        s.close()
        self.gone()

    def test_every_connection_has_the_safe_settings(self):
        """secure_delete ON and temp_store MEMORY on every kind of connection
        (a temporary file is only made for large sorts - so check the setting)."""
        s = self.w.open("ana")
        m = self.w.open("mod", "moderator")
        cons = [s.store._db, m.store._db, self.w.lib._reg, self.w.lib._shared_writer(),
                self.w.lib._outbox(self.w.lib._user(self.w.ids["ana"])["file_id"], False)]
        try:
            for c in cons:
                self.assertEqual(c.execute("PRAGMA secure_delete").fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA temp_store").fetchone()[0], 2)
        finally:
            for c in cons[3:]:
                c.close()
            s.close()
            m.close()

    def test_no_sqlite_temporary_files(self):
        tmp = tempfile.mkdtemp(prefix="sk_b52_tmpdir_")
        try:
            with mock.patch.dict(os.environ, {"TMPDIR": tmp, "SQLITE_TMPDIR": tmp}):
                s = self.w.open("ana")
                s.withdraw_source(self.w.ana_src)
                s.respond("Who maintains the Lantern Garden?")
                s.close()
            self.assertEqual(os.listdir(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_approval_racing_withdrawal(self):
        for n in range(15):
            s = self.w.open("ana")
            src = s.add_source("document", title=f"RACE-{n}-MARK", content_hash=f"race{n}")
            c = s.learn(f"Lio maintains the Race Room {n}.", "imported")
            s.add_evidence(c, "direct", src, f"Lio maintains the Race Room {n}.")
            tok = s.propose(c, {src: ["quote", "details"]})
            s.close()
            m = self.w.open("mod", "moderator")
            fid = [x for x in m.proposals() if x["token"] == tok][0]["file"]
            s = self.w.open("ana")
            t1 = threading.Thread(target=lambda: m.approve(fid, tok))
            t2 = threading.Thread(target=lambda: s.withdraw_source(src))
            for t in ((t1, t2) if n % 2 else (t2, t1)):
                t.start()
            t1.join()
            t2.join()
            s.finish_pending()
            s.close()
            m.close()
            shared = {p for p in self.w.files() if os.path.basename(p).startswith("shared.db")}
            self.assertEqual(self.w.where(f"RACE-{n}-MARK") & shared, set(), n)

    def test_moderators_never_read_or_approve_blocked_material(self):
        s = self.w.open("ana")
        src = s.add_source("document", title="BLOCKED-TITLE-MARK", content_hash="blk")
        c = s.learn("Nora maintains the Blocked Hall.", "imported")
        s.add_evidence(c, "direct", src, "Nora maintains the Blocked Hall.")
        tok = s.propose(c, {src: ["quote", "details"]})
        g = s.store._db.execute("SELECT opaque_id, token FROM main.source_grants WHERE "
                                "source_id=?", (src,)).fetchone()
        s.close()
        self.w.lib.block(g[0], g[1])                       # step 1 only (as after a crash)
        m = self.w.open("mod", "moderator")
        p = [x for x in m.proposals() if x["token"] == tok][0]
        self.assertEqual(p["material"], [])                # not readable any more
        m.approve(p["file"], tok)                          # approval copies nothing of it
        m.close()
        shared = {x for x in self.w.files() if os.path.basename(x).startswith("shared.db")}
        self.assertEqual(self.w.where("BLOCKED-TITLE-MARK") & shared, set())

    def _withdrawals(self):
        con = sqlite3.connect(self.w.lib.path("shared.db"))
        try:
            return con.execute("SELECT COUNT(*) FROM withdrawals").fetchone()[0]
        finally:
            con.close()

    def test_a_wrong_token_is_refused_and_leaves_no_trace(self):
        con = sqlite3.connect(self.w.lib.path("shared.db"))
        oid = con.execute("SELECT opaque_id FROM source_opaque").fetchone()[0]
        con.close()
        with self.assertRaises(BrainError):
            self.w.lib.block(oid, "not-the-token")
        self.assertEqual(self._withdrawals(), 0)

    def test_a_missing_token_record_is_refused_and_leaves_no_trace(self):
        with self.assertRaises(BrainError):                       # an id never registered
            self.w.lib.block("0" * 32, "any-token")
        self.assertEqual(self._withdrawals(), 0)
        con = sqlite3.connect(self.w.lib.path("shared.db"))       # a record removed
        oid = con.execute("SELECT opaque_id FROM source_opaque").fetchone()[0]
        con.execute("DELETE FROM source_tokens WHERE opaque_id=?", (oid,))
        con.commit()
        con.close()
        s = self.w.open("ana")
        tok = s.store._db.execute("SELECT token FROM main.source_grants WHERE opaque_id=?",
                                  (oid,)).fetchone()[0]
        s.close()
        with self.assertRaises(BrainError):                       # even with the real token
            self.w.lib.block(oid, tok)
        self.assertEqual(self._withdrawals(), 0)

    def test_outbox_only_material_is_registered_and_can_be_withdrawn(self):
        s = self.w.open("ana")
        src = s.add_source("document", title="OUTBOX-ONLY-MARK", content_hash="oo1")
        c = s.learn("Lio maintains the Outbox Hall.", "imported")
        s.add_evidence(c, "direct", src, "Lio maintains the Outbox Hall.")
        s.propose(c, {src: ["quote", "details"]})
        oid, tok = s.store._db.execute("SELECT opaque_id, token FROM main.source_grants WHERE "
                                       "source_id=?", (src,)).fetchone()
        with self.assertRaises(BrainError):                       # someone else's token
            self.w.lib.register_token(oid, self.w.lib.token_hash("intruder"))
        self.assertEqual(s.withdraw_source(src), "complete")
        s.close()
        own = self.w.personal_files("ana")
        self.assertEqual({p for p in self.w.where("OUTBOX-ONLY-MARK")} - own, set())


class WithdrawalPowerLoss(Tmp):
    """The process is killed at every step; then the next sessions finish it."""
    POINTS = ("block_before_commit", "after_block", "after_personal", "outbox_after_delete",
              "after_outbox", "purge_after_delete", "purge_before_complete")

    def run_point(self, point):
        w = W.World(os.path.join(self.d, point)).build()
        uid = w.ids["ana"]
        r = _child("withdraw", w.lib.path(), w.hk.hex(), w.lk.hex(), uid, str(w.ana_src),
                   crash=point)
        self.assertEqual(r.returncode, 77, r.stderr)
        committed = point != "block_before_commit"
        # immediately, before any recovery: the material is blocked (or nothing changed)
        m = w.open("mod", "moderator")
        self.assertEqual(m.proposals(), [])               # Ana's was decided already
        con = w.lib._outbox(w.lib._user(uid)["file_id"], write=False)
        oid = con.execute("SELECT opaque_id FROM material").fetchone()[0]
        con.close()
        blocked = oid in m._blocked()
        self.assertEqual(blocked, committed, point)
        m.close()
        l = w.open("luis")
        r2 = l.respond("Who maintains the Lantern Garden?")
        l.close()
        if committed:
            self.assertEqual(r2.reason, "evidence_withdrawn", point)
            self.assertEqual([e for c in r2.citations for e in c.evidence], [])
        else:
            self.assertEqual(r2.value, "Mira", point)     # not withdrawn: unchanged
            return w
        # recovery: any session finishes the shared steps, the owner's the rest
        l = w.open("luis")
        l.close()
        s = w.open("ana")
        s.finish_pending()
        s.close()
        own = w.personal_files("ana")
        for mk in W.GRANTED[:3]:
            self.assertEqual({os.path.relpath(p, self.d) for p in w.where(mk) - own}, set(),
                             (point, mk))
            self.assertTrue(w.where(mk) & own)
        s = w.open("ana")                                  # repeating is harmless
        self.assertEqual(s.finish_pending(), "complete")
        s.close()
        return w

    def test_every_point(self):
        for p in self.POINTS:
            with self.subTest(point=p):
                self.run_point(p)


# -- 8. migration --------------------------------------------------------------------------------------------
class Migration(Tmp):
    def make(self, name="data"):
        d = os.path.join(self.d, name)
        os.makedirs(d)
        b, ids, users = A.build(os.path.join(d, "knowledge.db"))
        b.close()
        return d, os.path.join(d, "knowledge.db")

    def test_success_original_untouched_rows_placed_once(self):
        d, src = self.make()
        before, listing = S.fingerprint(src), sorted(os.listdir(d))
        hk, lk = secrets.token_bytes(32), secrets.token_bytes(32)
        rep = S.split_file(src, d, hk, lk)
        self.assertEqual(S.fingerprint(src), before)
        self.assertTrue(rep["original_unchanged"])
        self.assertEqual(sorted(set(os.listdir(d)) - set(listing)),
                         sorted(["brain", "brain-backups", f"brain-anchor-{rep['migration_id']}.json"]))
        self.assertEqual(rep["rows"]["claims"], len(A.CLAIMS))
        self.assertEqual(S.verify_library(os.path.join(d, "brain"), hk), "verified")
        for name in ("B", "C"):
            p = os.path.join(d, "brain-backups", rep["migration_id"], "manifest.json") \
                if name == "B" else rep["anchor"]
            self.assertEqual(S.read_manifest(p, hk)[1]["state"], "complete")
        # Luis's private notes (a marker only he has) never reach shared.db
        for p in os.listdir(os.path.join(d, "brain")):
            if p.startswith("shared.db"):
                with open(os.path.join(d, "brain", p), "rb") as fh:
                    blob = fh.read()
                for m in (b"Luis's notes", b"Ana's notes", b"Nina maintains", b"Kai maintains"):
                    self.assertNotIn(m, blob)

    def test_refusals_change_nothing(self):
        d, src = self.make()
        before = S.fingerprint(src)
        hk, lk = secrets.token_bytes(32), secrets.token_bytes(32)
        os.makedirs(os.path.join(d, "brain"))
        with self.assertRaises(BrainError):
            S.split_file(src, d, hk, lk)                       # brain folder exists
        os.rmdir(os.path.join(d, "brain"))
        import collections
        usage = collections.namedtuple("usage", "total used free")(0, 0, 10)
        with mock.patch("shutil.disk_usage", return_value=usage):
            with self.assertRaises(BrainError):
                S.split_file(src, d, hk, lk)                   # disk full
        self.assertEqual(S.fingerprint(src), before)
        self.assertFalse(os.path.exists(os.path.join(d, "brain")))

    def test_failure_mid_way_removes_staging_original_and_backup_kept(self):
        d, src = self.make()
        before = S.fingerprint(src)
        hk, lk = secrets.token_bytes(32), secrets.token_bytes(32)
        with mock.patch.object(S, "_verify", side_effect=BrainError("simulated corrupt row")):
            with self.assertRaises(BrainError):
                S.split_file(src, d, hk, lk)
        self.assertEqual(S.fingerprint(src), before)
        self.assertFalse([n for n in os.listdir(d) if n.startswith("brain-staging")])
        self.assertFalse(os.path.exists(os.path.join(d, "brain")))
        bak = os.listdir(os.path.join(d, "brain-backups"))[0]
        self.assertEqual(_sha(os.path.join(d, "brain-backups", bak, "knowledge.db")),
                         before["db"][2])

    def test_original_changed_during_migration_stops(self):
        d, src = self.make()
        hk, lk = secrets.token_bytes(32), secrets.token_bytes(32)
        real = S._build

        def touch(*a, **kw):
            r = real(*a, **kw)
            with open(src, "ab") as fh:
                fh.write(b"\0")                                 # another program writes
            return r
        with mock.patch.object(S, "_build", side_effect=touch):
            with self.assertRaises(BrainError):
                S.split_file(src, d, hk, lk)
        self.assertFalse(os.path.exists(os.path.join(d, "brain")))

    def test_shared_claim_with_private_evidence_is_refused(self):
        d, src = self.make()
        con = sqlite3.connect(src)        # a deliberately broken COPY? no: build a new file
        con.close()
        d2 = os.path.join(self.d, "bad")
        os.makedirs(d2)
        bad = os.path.join(d2, "knowledge.db")
        shutil.copyfile(src, bad)
        con = sqlite3.connect(bad)
        sid = con.execute("SELECT id FROM sources WHERE private=1").fetchone()[0]
        cid = con.execute("SELECT id FROM claims WHERE scope='shared'").fetchone()[0]
        con.execute("INSERT INTO evidence (claim_id, source_id, kind, locator, added_by, added_at) "
                    "VALUES (?,?, 'direct', 'x', 'system', '')", (cid, sid))
        con.commit()
        con.close()
        before = S.fingerprint(bad)
        with self.assertRaises(BrainError):
            S.split_file(bad, d2, secrets.token_bytes(32), secrets.token_bytes(32))
        self.assertEqual(S.fingerprint(bad), before)
        self.assertFalse(os.path.exists(os.path.join(d2, "brain")))

    def test_a_leftover_journal_stays_untouched(self):
        d, src = self.make()
        with open(src + "-journal", "wb") as fh:
            fh.write(b"")                                      # an empty leftover journal
        before = S.fingerprint(src)
        S.split_file(src, d, secrets.token_bytes(32), secrets.token_bytes(32))
        self.assertEqual(S.fingerprint(src), before)

    def test_power_loss_at_every_step(self):
        for point in ("after_manifests", "staging_rows", "before_manifest_a", "before_commit",
                      "after_commit"):
            with self.subTest(point=point):
                d, src = self.make(point)
                before = S.fingerprint(src)
                hk, lk = secrets.token_bytes(32), secrets.token_bytes(32)
                r = _child("split", src, d, hk.hex(), lk.hex(), crash=point)
                self.assertEqual(r.returncode, 77, r.stderr)
                self.assertEqual(S.fingerprint(src), before)
                rep = S.recovery_check(d, hk)
                self.assertTrue(rep["original"]["matches_record"], point)
                self.assertTrue(rep["verified_copy"], point)
                self.assertNotIn("delete_damaged_brain", rep["options"])
                if point == "after_commit":
                    self.assertEqual(S.verify_library(os.path.join(d, "brain"), hk), "verified")
                    for p in (os.path.join(d, "brain-backups", rep["migration_id"],
                                           "manifest.json"),
                              os.path.join(d, f"brain-anchor-{rep['migration_id']}.json")):
                        self.assertEqual(S.read_manifest(p, hk)[1]["state"], "complete")
                else:
                    self.assertFalse(os.path.exists(os.path.join(d, "brain")))
                    if any(n.startswith("brain-staging") for n in os.listdir(d)):
                        # without the host key the manifests can't be authenticated: kept
                        self.assertFalse(S.remove_unfinished_staging(d, None))
                        self.assertTrue(any(n.startswith("brain-staging")
                                            for n in os.listdir(d)))
                        self.assertTrue(S.remove_unfinished_staging(d, hk))
                    with self.assertRaises(BrainError):     # an unfinished one is never ignored
                        if any(n.startswith("brain-staging") for n in os.listdir(d)):
                            raise BrainError("still there")
                        S.split_file(src, d, hk, lk)
                        raise BrainError("ok: migrated again")
                self.assertEqual(S.fingerprint(src), before)


# -- 9. manifests and recovery ---------------------------------------------------------------------------------
class Recovery(Tmp):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.d, "data")
        os.makedirs(self.data)
        b, ids, users = A.build(os.path.join(self.data, "knowledge.db"))
        b.close()
        self.src = os.path.join(self.data, "knowledge.db")
        self.hk, self.lk = secrets.token_bytes(32), secrets.token_bytes(32)
        self.rep = S.split_file(self.src, self.data, self.hk, self.lk)
        self.mig = self.rep["migration_id"]
        self.A = os.path.join(self.data, "brain", "manifest.json")
        self.B = os.path.join(self.data, "brain-backups", self.mig, "manifest.json")
        self.C = os.path.join(self.data, f"brain-anchor-{self.mig}.json")
        self.bak = os.path.join(self.data, "brain-backups", self.mig, "knowledge.db")
        self.orig_sha, self.bak_sha = _sha(self.src), _sha(self.bak)

    def tearDown(self):
        # every case: the original and the backup are byte-identical
        if os.path.exists(self.src):
            self.assertEqual(_sha(self.src), self.orig_sha)
        if os.path.exists(self.bak):
            self.assertEqual(_sha(self.bak), self.bak_sha)
        super().tearDown()

    def corrupt(self, p):
        with open(p, "r+b") as fh:
            fh.seek(30)
            fh.write(b"XXXX")

    def resign(self, p, key, change=None):
        with open(p, encoding="utf-8") as fh:
            body = json.load(fh)["body"]
        if change:
            change(body)
        S.write_manifest(p, body, key)

    def damage_a_file(self):
        p = os.path.join(self.data, "brain", "shared.db")
        with open(p, "r+b") as fh:
            fh.seek(4096)
            fh.write(os.urandom(4096))

    def remove_a_file(self):
        fid = [n for n in os.listdir(os.path.join(self.data, "brain", "outbox"))][0]
        os.remove(os.path.join(self.data, "brain", "outbox", fid))

    def test_intact_library_offers_no_deletion(self):
        rep = S.recovery_check(self.data, self.hk)
        self.assertEqual(rep["manifests"], {"A": "authentic", "B": "authentic", "C": "authentic"})
        self.assertNotIn("delete_damaged_brain", rep["options"])
        self.assertFalse(rep["new_data_possible"])

    def test_deletion_only_when_all_five_conditions_hold(self):
        self.remove_a_file()                       # damage without new data
        rep = S.recovery_check(self.data, self.hk)
        self.assertIn("delete_damaged_brain", rep["options"])
        self.assertTrue(S.os.path.isdir(os.path.join(self.data, "brain")))   # nothing deleted
        self.assertFalse(S.delete_damaged_brain(self.data, self.hk))          # not confirmed
        self.assertTrue(S.delete_damaged_brain(self.data, self.hk, confirm=True))

    def test_new_data_or_damage_blocks_deletion(self):
        lib = Library(os.path.join(self.data, "brain"), self.hk, self.lk)
        u = lib.users()[0]["user_id"]
        s = lib.open_session(mint_grant(self.hk, lib.installation_id, lib.epoch, u, "user"))
        s.learn("Something new after the migration.", "user")
        s.close()
        lib.close()
        self.remove_a_file()
        rep = S.recovery_check(self.data, self.hk)
        self.assertTrue(rep["new_data_possible"])
        self.assertNotIn("delete_damaged_brain", rep["options"])
        self.assertIn("set_brain_aside", rep["options"])
        self.damage_a_file()
        rep = S.recovery_check(self.data, self.hk)
        self.assertNotIn("delete_damaged_brain", rep["options"])

    def test_manifest_trust_matrix(self):
        cases = {
            "A": lambda: self.corrupt(self.A), "B": lambda: self.corrupt(self.B),
            "C": lambda: self.corrupt(self.C),
            "A+B": lambda: (self.corrupt(self.A), self.corrupt(self.B)),
            "A+C": lambda: (self.corrupt(self.A), self.corrupt(self.C)),
            "B+C": lambda: (self.corrupt(self.B), self.corrupt(self.C)),
            "all": lambda: (self.corrupt(self.A), self.corrupt(self.B), self.corrupt(self.C)),
        }
        for name, act in cases.items():
            with self.subTest(case=name):
                self.setUp()
                act()
                self.remove_a_file()
                rep = S.recovery_check(self.data, self.hk)
                trusted = [k for k, t in rep["manifests"].items() if t == "authentic"]
                if name == "all":
                    self.assertEqual(trusted, [])
                    self.assertFalse(rep["verified_copy"])
                    self.assertTrue(rep.get("original_equals_backup") in (None, True))
                    self.assertNotIn("delete_damaged_brain", rep["options"])
                    self.assertIn("rebuild_from_original_unverified", rep["options"])
                else:
                    self.assertTrue(rep["verified_copy"], name)
                if "A" in name or name == "all":
                    # no authentic COMPLETE record of the files: may hold new data
                    if not any(k in trusted and rep["states"][k] == "complete" for k in "BC"):
                        self.assertNotIn("delete_damaged_brain", rep["options"])

    def test_wrong_hmac_unauthenticated_and_disagreement(self):
        self.resign(self.B, secrets.token_bytes(32))         # checksum fine, HMAC wrong
        self.remove_a_file()
        rep = S.recovery_check(self.data, self.hk)
        self.assertEqual(rep["manifests"]["B"], "untrusted")
        rep = S.recovery_check(self.data, None)               # the host key is unavailable
        # without the key nothing can be authenticated: valid checksums are only "intact"
        self.assertEqual(set(rep["manifests"].values()), {"unauthenticated"})
        self.assertNotIn("delete_damaged_brain", rep["options"])
        self.assertFalse(S.remove_unfinished_staging(self.data, None))
        self.setUp()
        self.resign(self.C, self.hk, lambda b: b["start"].update({"format": 99}))
        self.remove_a_file()
        rep = S.recovery_check(self.data, self.hk)
        self.assertIn("the authentic manifests disagree", rep["anomalies"])
        self.assertNotIn("delete_damaged_brain", rep["options"])

    def test_pending_and_complete_copies_compare_on_shared_fields(self):
        self.resign(self.B, self.hk, lambda b: (b.update({"state": "pending"}), b.pop("commit")))
        self.resign(self.C, self.hk, lambda b: (b.update({"state": "pending"}), b.pop("commit")))
        rep = S.recovery_check(self.data, self.hk)
        self.assertEqual(rep["anomalies"], [])               # a pending copy is not a conflict
        self.assertEqual(S.verify_library(os.path.join(self.data, "brain"), self.hk), "verified")
        self.assertEqual(S.read_manifest(self.B, self.hk)[1]["state"], "complete")
        self.resign(self.C, self.hk, lambda b: b.update({"state": "weird"}))
        self.remove_a_file()
        rep = S.recovery_check(self.data, self.hk)
        self.assertTrue(rep["anomalies"])
        self.assertNotIn("delete_damaged_brain", rep["options"])

    def test_original_or_backup_damaged(self):
        self.remove_a_file()
        os.rename(self.src, self.src + ".away")
        rep = S.recovery_check(self.data, self.hk)
        self.assertTrue(rep["verified_copy"])                  # the backup verifies
        os.rename(self.src + ".away", self.src)
        os.rename(self.bak, self.bak + ".away")
        rep = S.recovery_check(self.data, self.hk)
        self.assertTrue(rep["verified_copy"])                  # the original verifies
        os.rename(self.bak + ".away", self.bak)

    def test_first_open_after_damage_needs_recovery(self):
        self.damage_a_file()
        with self.assertRaises(S.RecoveryNeeded):
            S.verify_library(os.path.join(self.data, "brain"), self.hk)

    # -- write-ahead logs: post-migration writes vs corruption --------------------------
    def _writes_in_the_log(self):
        """A person writes after the migration while another connection keeps the
        log open: the personal file keeps a NON-EMPTY write-ahead log."""
        lib = Library(os.path.join(self.data, "brain"), self.hk, self.lk)
        u = [x for x in lib.users() if lib._user(x["user_id"])["kind"] != "moderator"][0]
        path = lib.path("personal", u["file_id"] + ".db")
        holder = sqlite3.connect(path)
        holder.execute("SELECT COUNT(*) FROM claims").fetchone()
        s = lib.open_session(mint_grant(self.hk, lib.installation_id, lib.epoch, u["user_id"],
                                        "user"))
        for n in range(30):
            s.learn(f"A note written after the migration, number {n}.", "user")
        s.close()
        lib.close()
        self.assertGreater(os.path.getsize(path + "-wal"), 0)
        return holder, path

    def test_writes_after_migration_with_a_non_empty_log_are_verified(self):
        holder, path = self._writes_in_the_log()
        try:
            self.assertEqual(S.wal_state(path + "-wal"), "ok")
            self.assertEqual(S.verify_library(os.path.join(self.data, "brain"), self.hk),
                             "verified")
        finally:
            holder.close()

    def test_damaged_database_with_a_non_empty_log_needs_recovery(self):
        holder, path = self._writes_in_the_log()
        try:
            size = os.path.getsize(path)
            with open(path, "r+b") as fh:                 # every page but the first
                fh.seek(4096)
                fh.write(os.urandom(size - 4096))
            with self.assertRaises(S.RecoveryNeeded):
                S.verify_library(os.path.join(self.data, "brain"), self.hk)
        finally:
            holder.close()

    def test_damaged_log_needs_recovery(self):
        holder, path = self._writes_in_the_log()
        try:
            with open(path + "-wal", "r+b") as fh:        # inside the first frame's page
                fh.seek(32 + 24 + 100)
                fh.write(b"\xff" * 16)
            self.assertEqual(S.wal_state(path + "-wal"), "damaged")
            with self.assertRaises(S.RecoveryNeeded):
                S.verify_library(os.path.join(self.data, "brain"), self.hk)
            rep = S.recovery_check(self.data, self.hk)
            name = os.path.relpath(path, os.path.join(self.data, "brain"))
            self.assertFalse(rep["brain_files"][name]["integrity"])
            self.assertNotIn("delete_damaged_brain", rep["options"])
        finally:
            holder.close()

    def test_a_log_torn_by_a_power_cut_is_not_damage(self):
        holder, path = self._writes_in_the_log()
        try:
            size = os.path.getsize(path + "-wal")
            with open(path + "-wal", "r+b") as fh:        # the last frame half written
                fh.truncate(size - 100)
            self.assertEqual(S.wal_state(path + "-wal"), "torn_tail")
            self.assertEqual(S.verify_library(os.path.join(self.data, "brain"), self.hk),
                             "verified")
        finally:
            holder.close()

    def _hashes(self, path):
        return {x: _sha(path + x) for x in ("", "-wal") if os.path.exists(path + x)}

    def test_the_integrity_check_includes_a_valid_non_empty_log(self):
        holder, path = self._writes_in_the_log()
        try:
            before = self._hashes(path)
            live = holder.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            ok, rows = S._integrity_on_copy(path, probe="SELECT COUNT(*) FROM claims")
            ok0, rows0 = S._integrity_on_copy(path, with_log=False,
                                              probe="SELECT COUNT(*) FROM claims")
            self.assertTrue(ok)
            self.assertEqual(rows[0][0], live)              # the log's 30 rows are seen
            self.assertEqual(rows[0][0] - rows0[0][0], 30)  # (not in the main file alone)
            self.assertEqual(self._hashes(path), before)    # originals byte-identical
        finally:
            holder.close()

    def _corrupt_a_committed_page_in_the_log(self, path):
        """Garbage in a b-tree page held in a COMMITTED log frame, with every
        frame checksum recomputed: SQLite accepts and applies the log, so only an
        integrity check of the combined state can see the damage."""
        import struct
        with open(path + "-wal", "rb") as fh:
            data = bytearray(fh.read())
        magic, _v, psize, _q, salt1, salt2, c1, c2 = struct.unpack(">8I", data[:32])
        big = magic & 1 == 1
        fsize = 24 + psize
        frames = (len(data) - 32) // fsize
        target = None
        for n in range(frames):                             # a table b-tree leaf page, not page 1
            off = 32 + n * fsize
            pg = struct.unpack(">I", data[off:off + 4])[0]
            if pg > 1 and data[off + 24] == 0x0D:
                target = off
        self.assertIsNotNone(target)
        page = target + 24
        data[page + 1:page + 8] = b"\xff\xff\x7f\xff\x00\x00\x00"  # freeblock / cell count junk
        s0, s1 = c1, c2
        for n in range(frames):
            off = 32 + n * fsize
            if struct.unpack(">II", data[off + 8:off + 16]) != (salt1, salt2):
                break
            s0, s1 = S._wal_sum(bytes(data[off:off + 8]), big, s0, s1)
            s0, s1 = S._wal_sum(bytes(data[off + 24:off + fsize]), big, s0, s1)
            data[off + 16:off + 24] = struct.pack(">II", s0, s1)
        with open(path + "-wal", "wb") as fh:
            fh.write(data)

    def test_corruption_committed_in_the_log_is_found_though_the_file_alone_passes(self):
        holder, path = self._writes_in_the_log()
        try:
            self._corrupt_a_committed_page_in_the_log(path)
            before = self._hashes(path)
            self.assertEqual(S.wal_state(path + "-wal"), "ok")          # checksums valid
            self.assertTrue(S._integrity_on_copy(path, with_log=False)[0])  # file alone: fine
            self.assertFalse(S._integrity_on_copy(path)[0])             # file + log: damaged
            with self.assertRaises(S.RecoveryNeeded):
                S.verify_library(os.path.join(self.data, "brain"), self.hk)
            rep = S.recovery_check(self.data, self.hk)
            name = os.path.relpath(path, os.path.join(self.data, "brain"))
            self.assertFalse(rep["brain_files"][name]["integrity"])
            self.assertEqual(self._hashes(path), before)                # never changed
        finally:
            holder.close()

    def test_a_healthy_log_passes_and_nothing_is_changed(self):
        holder, path = self._writes_in_the_log()
        try:
            before = self._hashes(path)
            self.assertEqual(S.verify_library(os.path.join(self.data, "brain"), self.hk),
                             "verified")
            rep = S.recovery_check(self.data, self.hk)
            name = os.path.relpath(path, os.path.join(self.data, "brain"))
            self.assertTrue(rep["brain_files"][name]["integrity"])
            self.assertEqual(self._hashes(path), before)
        finally:
            holder.close()

    def _concurrent(self, path, action, after_n=1):
        """Interpose `action` on the SOURCE files after the n-th file copy of a check
        (a write or checkpoint by SK at that moment)."""
        real = S._copy_file
        calls = []

        def copy(a, b):
            real(a, b)
            if a in (path, path + "-wal"):                 # copies of THIS file only
                calls.append(a)
                if len(calls) == after_n:
                    action()
        return mock.patch.object(S, "_copy_file", side_effect=copy)

    def _write(self, path):
        def go():
            con = sqlite3.connect(path)
            con.execute("INSERT INTO claim_events (claim_id, event, by, at) VALUES "
                        "(1, 'note', 'system', 'now')")
            con.commit()
            con.close()
        return go

    def _checkpoint(self, path):
        def go():
            con = sqlite3.connect(path)
            busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            con.close()
            self.assertEqual(busy, 0)
        return go

    def test_a_change_while_copying_is_refused(self):
        cases = (("write between the database and its log", "_write", 1),
                 ("checkpoint between the database and its log", "_checkpoint", 1),
                 ("write after both were copied", "_write", 2),
                 ("checkpoint after both were copied", "_checkpoint", 2))
        for label, how, n in cases:
            with self.subTest(case=label):
                self.setUp()
                holder, path = self._writes_in_the_log()
                more = self._write(path)                       # fresh rows in the log each time
                try:
                    more()
                    with self._concurrent(path, getattr(self, how)(path), n):
                        with self.assertRaises(S.ChangedWhileChecking):
                            S._integrity_on_copy(path)
                    more()
                    with self._concurrent(path, getattr(self, how)(path), n):
                        with self.assertRaises(S.ChangedWhileChecking):
                            S.verify_library(os.path.join(self.data, "brain"), self.hk)
                    # recovery_check: reported as not checked - never "damaged", never deletable
                    self.remove_a_file()
                    more()
                    with self._concurrent(path, getattr(self, how)(path), n):
                        rep = S.recovery_check(self.data, self.hk)
                    name = os.path.relpath(path, os.path.join(self.data, "brain"))
                    self.assertIsNone(rep["brain_files"][name]["integrity"])
                    self.assertTrue(any("changed while" in a for a in rep["anomalies"]))
                    self.assertNotIn("delete_damaged_brain", rep["options"])
                    # with SK idle, the same check passes
                    self.assertTrue(S._integrity_on_copy(path)[0])
                finally:
                    holder.close()

    def test_a_file_that_could_not_be_checked_never_allows_deletion(self):
        """Deletion would otherwise be offered (a file missing, the rest unchanged); if
        one present file could not be checked because it changed while being copied,
        nothing may be deleted on that basis."""
        self.remove_a_file()
        self.assertIn("delete_damaged_brain", S.recovery_check(self.data, self.hk)["options"])
        real = S._check_with_log
        target = os.path.join(self.data, "brain", "shared.db")

        def flaky(p):
            if p == target:
                raise S.ChangedWhileChecking("changed while it was being copied")
            return real(p)
        with mock.patch.object(S, "_check_with_log", side_effect=flaky):
            rep = S.recovery_check(self.data, self.hk)
        self.assertIsNone(rep["brain_files"]["shared.db"]["integrity"])
        self.assertNotIn("delete_damaged_brain", rep["options"])

    def test_no_change_no_refusal_and_the_copies_match(self):
        holder, path = self._writes_in_the_log()
        try:
            before = S._state_of(path)
            self.assertTrue(S._integrity_on_copy(path)[0])
            self.assertEqual(S._state_of(path), before)
        finally:
            holder.close()

    def test_wal_reader_rejects_bad_headers(self):
        p = os.path.join(self.d, "x.db")
        for blob, want in ((b"", "empty"), (b"short", "damaged"), (b"\0" * 64, "damaged")):
            with open(p + "-wal", "wb") as fh:
                fh.write(blob)
            self.assertEqual(S.wal_state(p + "-wal"), want, blob[:8])
        self.assertEqual(S.wal_state(os.path.join(self.d, "none.db-wal")), "absent")

    def test_set_aside_and_rescue_never_delete(self):
        rescued = S.rescue_readable_rows(self.data)
        self.assertTrue(os.path.isdir(rescued["rescued_to"]))
        aside = S.set_brain_aside(self.data)
        self.assertTrue(os.path.isdir(aside))
        self.assertFalse(os.path.exists(os.path.join(self.data, "brain")))


# -- key store ---------------------------------------------------------------------------------------------------
class KeyStore(Tmp):
    def test_round_trip_and_never_plain_on_windows(self):
        from puzzle_assistant.brain import keystore
        p = os.path.join(self.d, "keys", "host.key")
        k = keystore.load_or_create_key(p)
        self.assertEqual(len(k), 32)
        self.assertEqual(keystore.load_or_create_key(p), k)
        with open(p, "rb") as fh:
            blob = fh.read()
        if sys.platform == "win32":
            self.assertTrue(blob.startswith(keystore.MAGIC_DPAPI))
            self.assertNotIn(k, blob)
        else:
            self.assertTrue(blob.startswith(keystore.MAGIC_PLAIN))
            self.assertEqual(os.stat(p).st_mode & 0o077, 0)        # owner only
        with self.assertRaises(OSError):
            keystore.unprotect(b"garbage")


# -- 10. concurrency, boundaries -------------------------------------------------------------------------------
class ConcurrencyAndBoundaries(Tmp):
    def test_answering_while_a_moderator_approves(self):
        w = W.World(self.d).build()
        errors = []

        def answer(who):
            try:
                s = w.open(who)
                for _ in range(20):
                    s.respond("Who maintains the Lantern Garden?")
                s.close()
            except Exception as exc:                          # noqa: BLE001
                errors.append(repr(exc))

        def moderate():
            try:
                a = w.open("ana")
                toks = [a.propose(a.learn(f"Lio maintains the Hall {n}.", "user"), {})
                        for n in range(5)]
                a.close()
                m = w.open("mod", "moderator")
                fid = w.lib._user(w.ids["ana"])["file_id"]
                for t in toks:
                    m.approve(fid, t)
                m.close()
            except Exception as exc:                          # noqa: BLE001
                errors.append(repr(exc))
        ts = [threading.Thread(target=answer, args=(x,)) for x in ("luis", "eve")] + \
            [threading.Thread(target=moderate)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        for p in w.files():
            if p.endswith(".db"):
                con = sqlite3.connect(p)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok", p)
                con.close()

    def test_answering_never_writes_in_a_session(self):
        w = W.World(self.d).build()
        s = w.open("luis")
        s.respond("warm up")
        files = [s.store.path, w.lib.path("shared.db")]
        before = [_sha(p) for p in files] + [s.store.data_version()]
        for q, *_ in A.QUESTIONS[:20]:
            s.respond(q)
        self.assertEqual([_sha(p) for p in files] + [s.store.data_version()], before)
        s.close()

    def test_no_network_code_in_the_new_modules(self):
        for name in ("library.py", "split.py"):
            tree = ast.parse(open(os.path.join(BRAIN_DIR, name), encoding="utf-8").read())
            mods = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in n.names} | {n.module or "" for n in ast.walk(tree)
                                         if isinstance(n, ast.ImportFrom)}
            for bad in ("socket", "ssl", "http", "urllib", "requests", "subprocess"):
                self.assertFalse(any(m == bad or m.startswith(bad + ".") for m in mods), name)

    def test_the_production_default_is_refused(self):
        from puzzle_assistant.brain import Brain
        with self.assertRaises(BrainError):
            Brain()


if __name__ == "__main__":
    unittest.main()
