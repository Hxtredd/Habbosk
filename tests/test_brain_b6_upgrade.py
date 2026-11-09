"""B6: upgrading a REAL B5.2 library (tests/fixtures/b52_library.zip, written by the
B5.2 code at 3d75dd4) - as a verified copy, with power loss at every step.

Required every time: a complete original library or a verified upgraded library
survives as brain\\; the old library and the backup are never deleted; the
answers are the ones the B5.2 code gave; nothing personal reaches shared.db."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain import upgrade as UP  # noqa: E402
from puzzle_assistant.brain.library import Library, mint_grant  # noqa: E402
from puzzle_assistant.brain.store import BrainError, KnowledgeStore, NeedsUpgrade  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "b52_library.zip")
POINTS = ("after_backup", "after_journal_started", "staging_copied", "after_first_file",
          "after_verified", "after_swapping", "between_renames", "after_second_rename")


def raw_state(root):
    """Every file under root, byte for byte (sidecars included)."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_b6_up_")
        with zipfile.ZipFile(FIXTURE) as z:
            z.extractall(self.d)
        with open(os.path.join(self.d, "fixture.json"), encoding="utf-8") as fh:
            self.info = json.load(fh)
        os.remove(os.path.join(self.d, "fixture.json"))
        self.hk, self.lk = bytes.fromhex(self.info["hk"]), bytes.fromhex(self.info["lk"])
        self.root = os.path.join(self.d, "brain")
        self.before = UP.tree_fingerprint(self.root)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def answers_match(self):
        lib = Library(self.root, self.hk, self.lk)
        try:
            for who, ans in self.info["answers"].items():
                s = lib.open_session(mint_grant(self.hk, lib.installation_id, lib.epoch,
                                                self.info["ids"][who], "user"))
                for q, want in ans.items():
                    r = s.respond(q)
                    self.assertEqual([r.category, r.reason, r.value], want, (who, q))
                s.close()
        finally:
            lib.close()

    def is_original(self):
        return UP.tree_fingerprint(self.root) == self.before

    def survivors_intact(self):
        """The backup (and an old library, if any) are never deleted or changed."""
        backups = os.path.join(self.d, "brain-backups")
        for n in os.listdir(backups) if os.path.isdir(backups) else []:
            b = os.path.join(backups, n, "brain")
            if os.path.isdir(b):
                self.assertEqual(UP.tree_fingerprint(b), self.before, n)
        for n in os.listdir(self.d):
            if n.startswith("brain-pre-b6-"):
                self.assertEqual(UP.tree_fingerprint(os.path.join(self.d, n)), self.before)


class Refusal(Base):
    def test_a_b52_library_is_refused_and_left_byte_identical(self):
        before = raw_state(self.root)
        listing = sorted(os.listdir(self.d))
        with self.assertRaises(NeedsUpgrade):
            Library(self.root, self.hk, self.lk)
        for name in ("shared.db",) + tuple(
                os.path.join("personal", n) for n in os.listdir(os.path.join(self.root,
                                                                             "personal"))
                if n.endswith(".db")):
            with self.assertRaises(NeedsUpgrade):
                KnowledgeStore(os.path.join(self.root, name),
                               role="shared" if name == "shared.db" else "personal",
                               owner=("x", "x", "local_user"))
        self.assertEqual(raw_state(self.root), before)          # sidecars included
        self.assertEqual(sorted(os.listdir(self.d)), listing)


class Upgrade(Base):
    def test_upgrade_keeps_every_row_and_every_answer(self):
        rep = UP.upgrade_library(self.d, self.hk)
        self.answers_match()
        self.assertEqual(UP.tree_fingerprint(rep["old_library"]), self.before)
        self.assertEqual(UP.tree_fingerprint(rep["backup"]), self.before)
        with self.assertRaises(BrainError):
            UP.upgrade_library(self.d, self.hk)                 # already B6
        self.assertEqual(UP.unfinished(self.d), [])

    def test_separation_kept_after_upgrade(self):
        UP.upgrade_library(self.d, self.hk)
        lib = Library(self.root, self.hk, self.lk)
        files = {}
        for u, uid in self.info["ids"].items():
            fid = lib._user(uid)["file_id"]
            files[u] = {p for p in raw_state(self.root) if fid in p}
        lib.close()
        for owner, markers in self.info["markers"].items():
            for m in markers:
                hits = {p for p, _ in raw_state(self.root).items()
                        if m.encode() in open(os.path.join(self.root, p), "rb").read()}
                self.assertEqual(hits - files[owner], set(), (owner, m))
        import sqlite3
        con = sqlite3.connect(os.path.join(self.root, "shared.db"))
        try:
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("source_files", names)
            self.assertNotIn("claim_candidates", names)
            UP.shared_scan(con)
        finally:
            con.close()

    def test_a_change_while_copying_is_refused_and_nothing_is_kept(self):
        real = UP._copy_tree

        def busy(src, dst):
            real(src, dst)
            with open(os.path.join(self.root, "epoch.json"), "a") as fh:
                fh.write(" ")                                  # SK wrote meanwhile
        with mock.patch.object(UP, "_copy_tree", side_effect=busy):
            with self.assertRaises(BrainError):
                UP.upgrade_library(self.d, self.hk)
        self.assertEqual(UP.unfinished(self.d), [])
        self.assertFalse([n for n in os.listdir(self.d) if n.startswith("brain-upgrade")])

    def test_disk_full_is_refused(self):
        import collections
        u = collections.namedtuple("u", "total used free")(0, 0, 10)
        with mock.patch("shutil.disk_usage", return_value=u):
            with self.assertRaises(BrainError):
                UP.upgrade_library(self.d, self.hk)
        self.assertTrue(self.is_original())


class PowerLoss(Base):
    def crash(self, point):
        r = subprocess.run([sys.executable, os.path.join(HERE, "b6_child.py"), "upgrade",
                            self.d, self.hk.hex()],
                           env=dict(os.environ, SK_TEST_CRASH_AT=point), capture_output=True,
                           text=True, timeout=600)
        self.assertEqual(r.returncode, 77, r.stderr)

    def test_every_interruption_point(self):
        for point in POINTS:
            with self.subTest(point=point):
                self.tearDown()
                self.setUp()
                self.crash(point)
                if point != "after_backup":
                    with self.assertRaises(UP.UpgradePending):
                        Library(self.root, self.hk, self.lk)    # never used half-way
                rep = UP.recover_upgrade(self.d, self.hk)
                self.assertEqual(rep["report_only"], [], point)
                self.survivors_intact()
                # a complete original OR a verified upgraded library is in place
                if self.is_original():
                    with self.assertRaises(NeedsUpgrade):
                        Library(self.root, self.hk, self.lk)
                    UP.upgrade_library(self.d, self.hk)         # and can be upgraded again
                self.answers_match()
                self.assertEqual(UP.recover_upgrade(self.d, self.hk),
                                 {"recovered": [], "report_only": []})   # nothing left to do
                self.survivors_intact()

    def test_between_renames_with_a_damaged_staging_copy_rolls_back(self):
        self.crash("between_renames")
        staging = [n for n in os.listdir(self.d) if n.startswith("brain-upgrade-staging-")][0]
        with open(os.path.join(self.d, staging, "shared.db"), "r+b") as fh:
            fh.seek(5000)
            fh.write(os.urandom(2000))
        rep = UP.recover_upgrade(self.d, self.hk)
        self.assertEqual(rep["recovered"][0]["action"], "the original library was put back")
        self.assertTrue(self.is_original())
        self.assertTrue(os.path.isdir(os.path.join(self.d, staging)))   # kept, never deleted
        self.survivors_intact()

    def test_between_renames_with_damaged_journals_changes_nothing(self):
        self.crash("between_renames")
        listing = sorted(os.listdir(self.d))
        uid = UP.unfinished(self.d)[0]
        for p in UP._journals(self.d, uid):
            with open(p, "r+b") as fh:
                fh.seek(40)
                fh.write(b"XXXX")
        rep = UP.recover_upgrade(self.d, self.hk)
        self.assertEqual(rep["recovered"], [])
        self.assertEqual(len(rep["report_only"]), 1)
        self.assertEqual(sorted(os.listdir(self.d)), listing)           # nothing moved
        self.survivors_intact()                                         # the original survives
        rep = UP.recover_upgrade(self.d, None)                          # no key: report only
        self.assertEqual(rep["recovered"], [])

    def test_one_damaged_journal_copy_is_enough(self):
        self.crash("between_renames")
        uid = UP.unfinished(self.d)[0]
        with open(UP._journals(self.d, uid)[0], "r+b") as fh:
            fh.seek(40)
            fh.write(b"XXXX")
        rep = UP.recover_upgrade(self.d, self.hk)
        self.assertEqual(rep["recovered"][0]["action"],
                         "the verified upgraded library was put in place")
        self.answers_match()

    def test_a_journal_signed_with_another_key_is_not_trusted(self):
        self.crash("after_swapping")
        rep = UP.recover_upgrade(self.d, os.urandom(32))
        self.assertEqual(rep["recovered"], [])
        self.assertTrue(self.is_original())


if __name__ == "__main__":
    unittest.main()
