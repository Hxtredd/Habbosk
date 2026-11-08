"""B3.5 read-only audit of knowledge.db (devtools/knowledge_audit.py and
devtools/safe_snapshot.py). Every database here is SYNTHETIC, created in a
temporary folder; the real knowledge.db is never used."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.devtools import knowledge_audit as KA  # noqa: E402
from puzzle_assistant.devtools import safe_snapshot as SS  # noqa: E402

SECRET = "ZQX-private-marker"        # must never appear in a report


def build(folder, extra=0):
    """A synthetic knowledge.db with private-looking text everywhere."""
    path = os.path.join(folder, "knowledge.db")
    b = Brain(path, "", "")
    other = b.store.add_contributor(f"{SECRET} person", "external_user")
    src = b.add_source("webpage", title=f"{SECRET} title", reference=f"https://{SECRET}.example/x",
                       raw_text=f"{SECRET} raw page text")
    sec = b.add_section(src, f"{SECRET} section text", f"{SECRET} > heading")
    c1 = b.learn(f"The {SECRET} Garden has room ID 900001.", "imported", f"{SECRET} Garden",
                 section_id=sec)
    b.add_evidence(c1, "direct", src, locator=f"{SECRET} quote")
    b.review(c1, "verified", note=f"{SECRET} note")
    b.learn(f"{SECRET} says something.", "user", by=other)
    for i in range(extra):
        b.learn(f"Extra {SECRET} claim {i}.", "user")
    b.close()
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sk_audit_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "SK Assistant # data")
        os.makedirs(self.data)
        self.temp_base = os.path.join(self.tmp, "local temp")
        os.makedirs(self.temp_base)
        self.db = build(self.data)

    def run_audit(self, **kw):
        kw.setdefault("temp_base", self.temp_base)
        return KA.audit(self.db, running=lambda: False, **kw)

    def assert_private_free(self, text):
        self.assertNotIn(SECRET.lower(), text.lower())
        self.assertNotIn(os.path.expanduser("~"), text) if len(os.path.expanduser("~")) > 3 else None


class InMemory(Base):
    def test_report_counts_and_nothing_written_anywhere(self):
        before = (SS.fingerprint(self.db), SS.listing(self.data))
        with mock.patch.object(SS.tempfile, "mkdtemp", side_effect=AssertionError("no disk copy")):
            code, text = self.run_audit()
        self.assertEqual(code, 0, text)
        self.assertIn("Read method: memory", text)
        self.assertIn("none was written to disk", text)
        self.assertIn("claims: 2", text)
        self.assertIn("by status: submitted 1, verified 1", text)
        self.assertIn("by origin: imported 1, user 1", text)
        self.assertIn("contributors by kind: external_user 1, importer 1, local_user 1, system 1", text)
        self.assertIn("sources by kind: webpage 1", text)
        self.assertIn("evidence by kind: direct 1", text)
        self.assertIn("integrity check: ok", text)
        self.assertIn("exactly unchanged (size, time, SHA-256): YES", text)
        self.assertIn("none created or removed: YES", text)
        self.assertEqual((SS.fingerprint(self.db), SS.listing(self.data)), before)
        self.assertEqual(SS.listing(self.temp_base), [])
        self.assert_private_free(text)

    def test_the_original_is_never_opened_with_sqlite(self):
        real = sqlite3.connect
        opened = []

        def spy(target, *a, **kw):
            opened.append(str(target))
            return real(target, *a, **kw)
        with mock.patch.object(SS.sqlite3, "connect", spy):
            code, _ = self.run_audit()
        self.assertEqual(code, 0)
        self.assertTrue(opened)
        for t in opened:
            self.assertFalse(os.path.abspath(t).startswith(os.path.abspath(self.data)), t)

    def test_wal_mode_database_with_empty_log_reads_in_memory(self):
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA journal_mode=WAL")
        con.close()                                   # last close: -wal checkpointed and removed
        before = SS.fingerprint(self.db)
        code, text = self.run_audit()
        self.assertEqual(code, 0, text)
        self.assertIn("Read method: memory", text)
        self.assertIn("claims: 2", text)
        self.assertEqual(SS.fingerprint(self.db), before)


class DiskCopy(Base):
    def test_uncheckpointed_wal_is_read_from_a_copy_and_originals_stay_untouched(self):
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("INSERT INTO claims (text, origin, status, scope, owner_id, version, created, "
                    "created_by, updated) SELECT 'wal only', 'user', 'submitted', 'personal', "
                    "owner_id, 1, created, created_by, updated FROM claims LIMIT 1")
        con.commit()                                  # committed, only in the -wal
        try:
            self.assertGreater(os.path.getsize(self.db + "-wal"), 0)
            before = (SS.fingerprint(self.db), SS.listing(self.data))
            code, text = self.run_audit()
            self.assertEqual(code, 0, text)
            self.assertIn("Read method: disk copy", text)
            self.assertIn("claims: 3", text)             # the -wal's change is counted
            self.assertIn("deleted (ordinary file deletion - NOT forensic erasure", text)
            self.assertEqual((SS.fingerprint(self.db), SS.listing(self.data)), before)
            self.assertEqual(SS.listing(self.temp_base), [])      # copy removed
            self.assert_private_free(text)
        finally:
            con.close()

    def test_hot_journal_is_rolled_back_on_the_copy_only(self):
        crash = textwrap.dedent("""
            import os, sqlite3, sys
            con = sqlite3.connect(sys.argv[1], isolation_level=None)
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("BEGIN IMMEDIATE")
            con.execute("DELETE FROM claim_events")
            con.execute("UPDATE claims SET status='rejected'")
            con.execute("PRAGMA cache_size=1")
            con.execute("CREATE TABLE filler(x)")
            con.executemany("INSERT INTO filler VALUES (?)", [("x" * 2000,)] * 500)
            os._exit(9)                                 # crash mid-transaction
        """)
        r = subprocess.run([sys.executable, "-c", crash, self.db], capture_output=True)
        self.assertEqual(r.returncode, 9, r.stderr)
        self.assertTrue(os.path.exists(self.db + "-journal"))
        before = (SS.fingerprint(self.db), SS.listing(self.data))
        code, text = self.run_audit()
        self.assertEqual(code, 0, text)
        self.assertIn("Read method: disk copy", text)
        self.assertIn("by status: submitted 1, verified 1", text)   # pre-crash state
        self.assertEqual((SS.fingerprint(self.db), SS.listing(self.data)), before)
        self.assertTrue(os.path.exists(self.db + "-journal"))      # original journal left alone

    def test_older_python_without_memory_reading_uses_a_copy(self):
        real = SS.open_snapshot
        with mock.patch.object(SS, "open_snapshot",
                               lambda p, t=None, **k: real(p, t, allow_memory=False)):
            code, text = self.run_audit()
        self.assertEqual(code, 0, text)
        self.assertIn("disk copy", text)
        self.assertIn("needs 3.11+", text)
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_temporary_copy_is_removed_even_after_an_error(self):
        real = SS.open_snapshot
        with mock.patch.object(SS, "open_snapshot",
                               lambda p, t=None, **k: real(p, t, allow_memory=False)), \
                mock.patch.object(KA, "analyse", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_audit()
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_leftover_from_an_interrupted_run_is_removed(self):
        left = os.path.join(self.temp_base, SS.TEMP_PREFIX + "old")
        os.makedirs(left)
        with open(os.path.join(left, "knowledge.db"), "w") as fh:
            fh.write("old copy")
        real = SS.open_snapshot
        with mock.patch.object(SS, "open_snapshot",
                               lambda p, t=None, **k: real(p, t, allow_memory=False)):
            code, text = self.run_audit()
        self.assertEqual(code, 0, text)
        self.assertIn("removed 1 leftover", text)
        self.assertEqual(SS.listing(self.temp_base), [])

    @unittest.skipIf(os.name != "posix", "POSIX permission bits")
    def test_temporary_folder_is_private(self):
        modes = []
        real_rmtree = SS.shutil.rmtree

        def look(path, *a, **kw):
            if os.path.basename(path).startswith(SS.TEMP_PREFIX) and os.path.isdir(path):
                modes.append(os.stat(path).st_mode & 0o777)
            return real_rmtree(path, *a, **kw)
        real = SS.open_snapshot
        with mock.patch.object(SS.shutil, "rmtree", look), \
                mock.patch.object(SS, "open_snapshot",
                                  lambda p, t=None, **k: real(p, t, allow_memory=False)):
            self.run_audit()
        self.assertEqual(modes, [0o700])


class CopyFailures(Base):
    def test_unreadable_copy_is_removed(self):
        with open(self.db, "r+b") as fh:            # valid header, damaged body
            fh.seek(100)
            fh.write(b"\xff" * 4000)
        with open(self.db + "-journal", "wb") as fh:
            fh.write(b"\x00" * 512)                 # forces the disk-copy path
        code, text = self.run_audit()
        self.assertEqual(code, 1, text)
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_failed_copy_is_removed(self):
        with open(self.db + "-journal", "wb") as fh:
            fh.write(b"\x00" * 512)
        real = SS.shutil.copyfile
        calls = []

        def half(src, dst, *a, **k):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(src, dst, *a, **k)
        with mock.patch.object(SS.shutil, "copyfile", half):
            with self.assertRaises(OSError):
                SS.open_snapshot(self.db, self.temp_base)
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_a_copy_that_differs_from_the_original_is_never_used(self):
        with open(self.db + "-journal", "wb") as fh:
            fh.write(b"\x00" * 512)
        real = SS.shutil.copyfile

        def corrupt(src, dst, *a, **k):
            real(src, dst, *a, **k)
            with open(dst, "ab") as fh:
                fh.write(b"x")                       # the copy is not the original
        with mock.patch.object(SS.shutil, "copyfile", corrupt):
            with self.assertRaises(SS.SnapshotRefused):
                SS.open_snapshot(self.db, self.temp_base)
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_an_original_changed_during_the_audit_is_reported(self):
        real = KA.analyse

        def meanwhile(con):
            other = sqlite3.connect(self.db)          # another program writes now
            other.execute("UPDATE claims SET category='changed'")
            other.commit()
            other.close()
            return real(con)
        with mock.patch.object(KA, "analyse", meanwhile):
            code, text = self.run_audit()
        self.assertEqual(code, 2)
        self.assertIn("ORIGINAL FILES CHANGED DURING THE AUDIT", text)
        self.assertIn("exactly unchanged (size, time, SHA-256): NO", text)


class TempFolderRules(Base):
    def wal(self):
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("UPDATE claims SET category='x'")
        con.commit()
        self.addCleanup(con.close)

    def test_cloud_synced_temp_folders_are_refused(self):
        self.wal()
        cases = []
        od = os.path.join(self.tmp, "my sync root")
        os.makedirs(od)
        cases.append(({"OneDrive": od}, od))
        for name in ("OneDrive - Contoso", "Dropbox", "Google Drive", "iCloudDrive", "Box"):
            d = os.path.join(self.tmp, name, "tmp")
            os.makedirs(d)
            cases.append(({}, d))
        before = (SS.fingerprint(self.db), SS.listing(self.data))
        for env, base in cases:
            with mock.patch.dict(os.environ, env):
                code, text = self.run_audit(temp_base=base)
            self.assertEqual(code, 1, text)
            self.assertIn("refusing to put a temporary copy there", text)
            self.assertEqual(os.listdir(base), [])
            self.assertEqual((SS.fingerprint(self.db), SS.listing(self.data)), before)

    def test_network_or_removable_media_is_refused(self):
        self.wal()
        with mock.patch.object(SS, "drive_reason", return_value="it is on a network drive"):
            code, text = self.run_audit()
        self.assertEqual(code, 1)
        self.assertIn("network drive", text)

    def test_a_chosen_local_folder_is_accepted(self):
        self.wal()
        mine = os.path.join(self.tmp, "chosen")
        os.makedirs(mine)
        code, text = self.run_audit(temp_base=mine)
        self.assertEqual(code, 0, text)
        self.assertEqual(os.listdir(mine), [])


class Refusals(Base):
    def test_refuses_while_sk_is_running_and_reads_nothing(self):
        with mock.patch.object(SS, "fingerprint", side_effect=AssertionError("read")):
            code, text = KA.audit(self.db, self.temp_base, running=lambda: True)
        self.assertEqual(code, 1)
        self.assertIn("SK Assistant is running", text)

    def test_database_that_keeps_changing_is_not_audited(self):
        real = SS.fingerprint
        n = {"i": 0}

        def moving(path):
            n["i"] += 1
            fp = real(path)
            name = os.path.basename(path)
            size, mt, sha = fp[name]
            fp[name] = (size, mt + n["i"], sha)        # looks different every time
            return fp
        with mock.patch.object(SS, "fingerprint", moving):
            s = None
            with self.assertRaises(SS.SnapshotRefused):
                s = SS.open_snapshot(self.db, self.temp_base)
            self.assertIsNone(s)
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_not_a_database(self):
        with open(self.db, "wb") as fh:
            fh.write(b"this is not sqlite " * 100)
        before = SS.fingerprint(self.db)
        code, text = self.run_audit()
        self.assertEqual(code, 1)
        self.assertIn("not a readable SQLite database", text)
        self.assertEqual(SS.fingerprint(self.db), before)
        self.assertEqual(SS.listing(self.temp_base), [])

    def test_no_database(self):
        os.remove(self.db)
        code, text = self.run_audit()
        self.assertEqual(code, 0)
        self.assertIn("nothing to audit", text)

    def test_report_is_never_saved_in_the_data_folder(self):
        r = KA.main(["--db", self.db, "--report", os.path.join(self.data, "r.txt")])
        self.assertEqual(r, 1)
        self.assertFalse(os.path.exists(os.path.join(self.data, "r.txt")))


class PrivacyOfTheReport(Base):
    def test_unknown_values_are_reported_as_other_never_as_text(self):
        con = sqlite3.connect(self.db)
        con.execute(f"UPDATE claims SET status='{SECRET}', origin='{SECRET}'")
        con.execute(f"UPDATE sources SET kind='{SECRET}'")
        con.execute(f"INSERT INTO meta VALUES ('x', '{SECRET}')")
        con.execute(f"UPDATE claims SET created='{SECRET}'")
        con.commit()
        con.close()
        code, text = self.run_audit()
        self.assertEqual(code, 0, text)
        self.assertIn("other 2", text)
        self.assertIn("created between unknown and unknown", text)
        self.assert_private_free(text)

    def test_paths_are_masked(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp}):
            code, text = self.run_audit()
        self.assertIn("%APPDATA%", text)
        self.assertNotIn(self.tmp, text)

    def test_bench_backups_are_listed_by_size_and_never_opened(self):
        shutil.copy(self.db, os.path.join(self.data, "knowledge.db.backup-20260101-120000"))
        with open(os.path.join(self.data, f"knowledge.db.backup-{SECRET}"), "w") as fh:
            fh.write("x")
        real = sqlite3.connect
        opened = []
        with mock.patch.object(SS.sqlite3, "connect",
                               lambda t, *a, **k: opened.append(str(t)) or real(t, *a, **k)):
            code, text = self.run_audit()
        self.assertIn("knowledge.db.backup-20260101-120000 (", text)
        self.assertIn("1 other backup-like file(s), not opened", text)
        self.assertFalse(any("backup" in o for o in opened))
        self.assert_private_free(text)


class WindowsCheckUsesTheSafeMethod(unittest.TestCase):
    def test_windows_check_does_not_open_real_files_with_sqlite(self):
        import ast
        path = os.path.join(ROOT, "puzzle_assistant", "devtools", "brain_windows_check.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "real_files")
        src = ast.get_source_segment(open(path, encoding="utf-8").read(), fn)
        self.assertNotIn("connect_readonly", src)
        self.assertNotIn("sqlite3.connect", src)
        self.assertIn("open_snapshot", src)
        self.assertNotIn('"knowledge.db", "claims"', src)

    def test_windows_check_real_files_on_synthetic_data(self):
        from puzzle_assistant.devtools import brain_windows_check as W
        tmp = tempfile.mkdtemp(prefix="sk_wincheck_test_")
        self.addCleanup(shutil.rmtree, tmp, True)
        con = sqlite3.connect(os.path.join(tmp, "habbosk.db"))
        con.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, text TEXT)")
        con.executemany("INSERT INTO questions (text) VALUES (?)", [("a",), ("b",)])
        con.commit()
        con.close()
        build(tmp)
        before = {f: SS.fingerprint(os.path.join(tmp, f)) for f in ("habbosk.db", "knowledge.db")}
        W.results.clear()
        with mock.patch.dict(os.environ, {"SK_ASSISTANT_DATA": tmp}):
            W.real_files()
        name, ok, detail = W.results[-1]
        self.assertTrue(ok, detail)
        self.assertIn("rows 2", detail)
        self.assertIn("knowledge.db: not read by this check", detail)
        self.assertEqual({f: SS.fingerprint(os.path.join(tmp, f)) for f in before}, before)


if __name__ == "__main__":
    unittest.main()
