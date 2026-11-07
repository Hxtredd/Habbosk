"""Safety of the temporary Brain test bench: backups before real writes,
no writes without a backup, backups never overwritten, checks leave the real
database alone, test contributors never reach the real database."""
import datetime as dt
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.devtools import bench_safety as S  # noqa: E402
from puzzle_assistant.devtools.brain_checks import (file_unchanged, readonly_check,  # noqa: E402
                                                    scenario_checks)


def sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def claims_in(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT text FROM claims ORDER BY id")]
    finally:
        con.close()


def contributors_in(path):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute("SELECT display_name FROM contributors"))
    finally:
        con.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.real = os.path.join(self.dir, "knowledge.db")
        b = Brain(self.real, "", "")
        b.learn("El Spa está en Avenida Espumia.", "imported", "Spa")
        b.close()
        self.original = sha(self.real)

    def backups(self):
        return sorted(f for f in os.listdir(self.dir) if ".backup-" in f)


class BackupTests(Base):
    def test_backup_is_made_before_the_first_real_write(self):
        s = S.Session("real_readonly", self.real)
        self.assertEqual(self.backups(), [])
        with self.assertRaises(S.WritesDisabled):              # read-only: refused
            s.guard.run_write(lambda: s.brain.learn("x", "user"))
        self.assertTrue(s.guard.enable())                       # user allows changes
        [bk] = self.backups()
        self.assertEqual(os.path.join(self.dir, bk), s.guard.backup_file)
        s.guard.run_write(lambda: s.brain.learn("El taxi es gratis.", "user", "taxi"))
        s.close()
        self.assertEqual(claims_in(os.path.join(self.dir, bk)),
                         ["El Spa está en Avenida Espumia."])   # state before the write
        self.assertEqual(len(claims_in(self.real)), 2)

    def test_real_write_session_backs_up_before_opening(self):
        s = S.Session("real_write", self.real)
        [bk] = self.backups()
        self.assertEqual(claims_in(os.path.join(self.dir, bk)), claims_in(self.real))
        s.close()

    def test_failed_backup_prevents_every_write(self):
        def broken(path):
            raise OSError("disk full")
        g = S.WriteGuard(self.real, backup=broken)
        self.assertFalse(g.enable())
        self.assertIn("disk full", g.error)
        b = Brain(self.real, "", "")
        with self.assertRaises(S.WritesDisabled):
            g.run_write(lambda: b.learn("should not be saved", "user"))
        b.close()
        self.assertEqual(sha(self.real), self.original)
        saved = S.backup_database
        S.backup_database = broken                     # a whole "allow changes" session
        try:
            with self.assertRaises(S.WritesDisabled):
                S.Session("real_write", self.real)     # refused before the Brain opens it
        finally:
            S.backup_database = saved
        self.assertEqual(sha(self.real), self.original)
        self.assertEqual(self.backups(), [])           # no half backup left behind

    def test_existing_backups_are_never_overwritten(self):
        now = dt.datetime(2026, 11, 8, 10, 0, 0)
        first = S.backup_path_for(self.real, now)
        with open(first, "w") as fh:
            fh.write("an older backup")
        made = S.backup_database(self.real, now)
        self.assertNotEqual(made, first)
        self.assertTrue(made.endswith("-2"))
        with open(first) as fh:
            self.assertEqual(fh.read(), "an older backup")      # untouched
        made3 = S.backup_database(self.real, now)
        self.assertTrue(made3.endswith("-3"))
        self.assertEqual(claims_in(made3), claims_in(self.real))

    def test_no_database_means_nothing_to_back_up_and_readonly_refuses(self):
        missing = os.path.join(self.dir, "none.db")
        self.assertIsNone(S.backup_database(missing))
        with self.assertRaises(S.WritesDisabled):
            S.Session("real_readonly", missing)
        self.assertFalse(os.path.exists(missing))                # not created


class SeparationTests(Base):
    def test_readonly_session_does_not_modify_the_file(self):
        s = S.Session("real_readonly", self.real)
        s.brain.search("Spa")
        s.brain.ask("¿Dónde está el Spa?")
        s.close()
        self.assertEqual(sha(self.real), self.original)

    def test_automated_checks_leave_the_real_database_unchanged(self):
        s = S.Session("real_readonly", self.real)
        res = file_unchanged(self.real, lambda: scenario_checks() + readonly_check(s.brain))
        s.close()
        self.assertTrue(all(ok for _, ok, _ in res), [r for r in res if not r[1]])
        self.assertEqual(res[-1][2], "unchanged")
        self.assertEqual(sha(self.real), self.original)
        self.assertEqual(self.backups(), [])

    def test_test_contributors_never_reach_the_real_database(self):
        before = contributors_in(self.real)
        s = S.Session("sandbox", self.real)
        self.assertTrue(s.guard.enabled)                         # sandbox: free to experiment
        self.assertEqual(claims_in(s.path), claims_in(self.real))   # it is a copy
        s.brain.store.add_contributor("Test contributor 1", "external_user")
        s.brain.learn("solo en el sandbox", "user")
        sandbox_dir = os.path.dirname(s.path)
        s.close()
        self.assertEqual(contributors_in(self.real), before)
        self.assertNotIn("solo en el sandbox", claims_in(self.real))
        self.assertFalse(os.path.exists(sandbox_dir))            # disposable copy deleted
        self.assertEqual(sha(self.real), self.original)
        self.assertEqual(self.backups(), [])


if __name__ == "__main__":
    unittest.main()
