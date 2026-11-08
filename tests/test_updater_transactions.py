"""Updates are all-or-nothing: checked before anything is touched, installed
as a transaction, undone on failure or after an interruption, and personal
data, settings and development files are never removed.

Everything happens in temporary folders; the real installation is never used.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from puzzle_assistant import updater  # noqa: E402

OLD = {   # an installed 1.0.0
    "app.py": "print('app 1')\n",
    "puzzle_assistant/__init__.py": '__version__ = "1.0.0"\n',
    "puzzle_assistant/a.py": "A = 1\n",
    "puzzle_assistant/gone.py": "G = 1\n",          # 2.0.0 no longer ships it
    "README.txt": "same\n",
    "assets/logo.bin": "\x00\x01old",
    "requirements.txt": "numpy>=1\n",
}
USER = {  # never the updater's to change
    ".venv/Scripts/python.exe": "venv",
    "config.json": '{"mine": true}',
    "data/memory.db": "my memory",
    "debug/frame.png": "debug",
    "_previous/puzzle_assistant/a.py": "A = 0  # the backup of the update before\n",
    "my_notes.txt": "a file the owner made",
    "puzzle_assistant/local_tool.py": "X = 1  # a developer's own file\n",
}
NEW = {   # release 2.0.0
    "app.py": "print('app 2')\n",
    "puzzle_assistant/__init__.py": '__version__ = "2.0.0"\n',
    "puzzle_assistant/a.py": "A = 2\n",
    "puzzle_assistant/b.py": "B = 2\n",
    "puzzle_assistant/sub/c.py": "C = 2\n",
    "README.txt": "same\n",
    "assets/logo.bin": "\x00\x01new",
    "requirements.txt": "numpy>=1\n",
}


def make_zip(files, top="SK Assistant", compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in files.items():
            z.writestr(f"{top}/{name}" if top else name, data)
    return buf.getvalue()


def write_tree(root, files):
    for rel, data in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data.encode("latin-1") if isinstance(data, str) else data)


def snapshot(root):
    out = {}
    for d, dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(d, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel == updater.LOCK:
                continue
            with open(full, "rb") as fh:
                out[rel] = fh.read()
        for sub in dirs:                       # empty folders count too
            full = os.path.join(d, sub)
            if not os.listdir(full):
                out[os.path.relpath(full, root).replace(os.sep, "/") + "/"] = b""
    return out


def manifest_for(files):
    return {"version": "1.0.0", "files": {
        rel: hashlib.sha256(data.encode("latin-1")).hexdigest() for rel, data in files.items()}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sk_upd_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.target = os.path.join(self.tmp, "SK Assistant # folder")
        os.makedirs(self.target)
        write_tree(self.target, OLD)
        write_tree(self.target, USER)

    def with_manifest(self):
        write_tree(self.target, {updater.MANIFEST: json.dumps(manifest_for(OLD))})

    def zip_file(self, data=None, name="u.zip"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(make_zip(NEW) if data is None else data)
        return path

    def install(self, data=None, **kw):
        kw.setdefault("packages", lambda changed, target: None)
        return updater.install(self.zip_file(data), self.target, **kw)

    def assert_clean(self, lock_held=False):
        for leftover in (updater.STAGING, updater.BACKUP, updater.JOURNAL, updater.LOCK,
                         updater.JOURNAL + ".tmp"):
            if lock_held and leftover == updater.LOCK:
                continue
            self.assertFalse(os.path.exists(os.path.join(self.target, leftover)), leftover)
        for d, _, files in os.walk(self.target):
            for f in files:
                self.assertFalse(f.endswith((".sk_new", ".part", ".tmp")), f)


class Success(Base):
    def test_update_installs_backs_up_and_keeps_user_files(self):
        self.with_manifest()
        changed = self.install(expected_version="2.0.0")
        snap = snapshot(self.target)
        for rel, data in NEW.items():
            self.assertEqual(snap[rel], data.encode("latin-1"), rel)
        self.assertNotIn("puzzle_assistant/gone.py", snap)           # removed by the release
        for rel, data in USER.items():
            if not rel.startswith("_previous/"):
                self.assertEqual(snap[rel], data.encode("latin-1"), rel)
        # _previous now holds exactly what this update replaced or removed
        prev = {k[len("_previous/"):]: v for k, v in snap.items() if k.startswith("_previous/")}
        self.assertEqual(prev, {
            "app.py": b"print('app 1')\n", "puzzle_assistant/__init__.py": b'__version__ = "1.0.0"\n',
            "puzzle_assistant/a.py": b"A = 1\n", "puzzle_assistant/gone.py": b"G = 1\n",
            "assets/logo.bin": b"\x00\x01old", updater.MANIFEST: snap["_previous/" + updater.MANIFEST]})
        self.assertEqual(sorted(c.replace(os.sep, "/") for c in changed), sorted([
            "app.py", "puzzle_assistant/__init__.py", "puzzle_assistant/a.py",
            "puzzle_assistant/b.py", "puzzle_assistant/sub/c.py", "assets/logo.bin",
            "puzzle_assistant/gone.py"]))
        man = json.loads(snap[updater.MANIFEST])
        self.assertEqual((man["version"], sorted(man["files"])), ("2.0.0", sorted(NEW)))
        self.assert_clean()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "u.zip")))  # download removed

    def test_a_first_update_without_a_record_removes_nothing(self):
        # installations made before this updater have no _installed_files.json
        self.install()
        snap = snapshot(self.target)
        self.assertEqual(snap["puzzle_assistant/gone.py"], b"G = 1\n")
        self.assertIn(updater.MANIFEST, snap)
        self.assert_clean()

    def test_release_files_named_like_protected_ones_are_ignored(self):
        evil = dict(NEW, **{"config.json": "{}", "data/memory.db": "x", ".venv/x": "x",
                            "_previous/x": "x", updater.MANIFEST: "{}",
                            updater.JOURNAL: "{}", "Config.JSON": "{}"})
        self.install(make_zip(evil))
        snap = snapshot(self.target)
        self.assertEqual(snap["config.json"], b'{"mine": true}')
        self.assertEqual(snap["data/memory.db"], b"my memory")
        self.assertNotIn(".venv/x", snap)
        self.assertNotIn("Config.JSON", snap)
        self.assertIn(updater.MANIFEST, snap)
        self.assertNotEqual(snap[updater.MANIFEST], b"{}")
        self.assert_clean()

    def test_removal_only_touches_unchanged_files_the_updater_installed(self):
        man = manifest_for(OLD)
        sha = lambda t: hashlib.sha256(t.encode()).hexdigest()  # noqa: E731
        # a damaged or hand-edited record names protected and outside files,
        # with their real checksums: they are still never removed
        man["files"].update({"my_notes.txt": "0" * 64, "config.json": sha('{"mine": true}'),
                             "../outside.txt": sha("outside"), "data/memory.db": sha("my memory"),
                             ".venv/Scripts/python.exe": sha("venv"),
                             "_previous/puzzle_assistant/a.py":
                                 sha("A = 0  # the backup of the update before\n")})
        write_tree(self.target, {updater.MANIFEST: json.dumps(man)})
        write_tree(self.tmp, {"outside.txt": "outside"})
        write_tree(self.target, {"puzzle_assistant/gone.py": "G = 1  # edited by a developer\n"})
        self.install()
        snap = snapshot(self.target)
        self.assertEqual(snap["puzzle_assistant/gone.py"], b"G = 1  # edited by a developer\n")
        self.assertEqual(snap["my_notes.txt"], b"a file the owner made")
        self.assertEqual(snap["config.json"], b'{"mine": true}')
        self.assertEqual(snap["data/memory.db"], b"my memory")
        self.assertIn(".venv/Scripts/python.exe", snap)
        self.assertEqual(snap["puzzle_assistant/local_tool.py"], b"X = 1  # a developer's own file\n")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "outside.txt")))

    def test_a_file_windows_keeps_open_is_overwritten_in_place(self):
        real = os.replace
        locked = os.path.join(self.target, "assets", "logo.bin")

        def replace(src, dst):
            if os.path.abspath(dst) == os.path.abspath(locked) and src.endswith(".sk_new"):
                raise PermissionError("in use")
            return real(src, dst)
        with mock.patch.object(updater.os, "replace", replace), \
                mock.patch.object(updater.time, "sleep"):
            self.install()
        self.assertEqual(snapshot(self.target)["assets/logo.bin"], b"\x00\x01new")
        self.assert_clean()

    def test_package_step_sees_the_new_files_before_commit(self):
        seen = {}

        def packages(changed, target):
            seen["a"] = open(os.path.join(target, "puzzle_assistant", "a.py")).read()
            seen["journal"] = os.path.exists(os.path.join(target, updater.JOURNAL))
        self.install(packages=packages)
        self.assertEqual(seen, {"a": "A = 2\n", "journal": True})


class Refused(Base):
    """Packages that are not right are refused before any program file changes."""

    def refused(self, data, lock_held=False, **kw):
        before = snapshot(self.target)
        with self.assertRaises(updater.UpdateError) as cm:
            self.install(data, **kw)
        self.assertEqual(snapshot(self.target), before)
        self.assert_clean(lock_held)
        self.assertIn("Nothing in the program folder was changed", str(cm.exception))
        self.assertFalse(cm.exception.installed_changed)
        return str(cm.exception)

    def test_not_a_zip(self):
        self.assertIn("not a valid ZIP", self.refused(b"this is not a zip file at all"))

    def test_truncated_zip(self):
        self.refused(make_zip(NEW)[:-200])

    def test_damaged_file_inside_zip(self):
        data = bytearray(make_zip(NEW, compression=zipfile.ZIP_STORED))
        i = data.find(b"A = 2")
        data[i + 4] = ord("9")                     # content no longer matches its CRC
        self.assertIn("damaged", self.refused(bytes(data)))

    def test_empty_zip(self):
        self.refused(make_zip({}))

    def test_not_an_sk_package(self):
        self.refused(make_zip({"something/else.txt": "x"}))

    def test_unsafe_names(self):
        for name in ("../escape.txt", "/abs.txt", "C:/win.txt", "a/../../x.txt", "a\\..\\..\\x"):
            files = dict(NEW)
            files[name] = "x"
            self.refused(make_zip(files, top=""))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_broken_program_code(self):
        self.assertIn("puzzle_assistant/b.py",
                      self.refused(make_zip(dict(NEW, **{"puzzle_assistant/b.py": "def (:\n"}))))

    def test_wrong_version_inside(self):
        self.assertIn("1.5.0", self.refused(
            make_zip(dict(NEW, **{"puzzle_assistant/__init__.py": '__version__ = "1.5.0"\n'})),
            expected_version="2.0.0"))

    def test_announced_version_may_omit_trailing_zeros(self):
        self.install(expected_version="2.0")
        self.assertIn(b'"2.0.0"', snapshot(self.target)["puzzle_assistant/__init__.py"])

    def test_another_update_already_running(self):
        lock = updater._Lock(self.target)
        self.assertTrue(lock.acquire())
        try:
            self.assertIn("already running", self.refused(None, lock_held=True))
        finally:
            lock.release()

    def test_broken_code_outside_the_program_is_not_a_reason_to_refuse(self):
        self.install(make_zip(dict(NEW, **{"tools/py313_only.py": "def (:\n"})))
        self.assertIn("tools/py313_only.py", snapshot(self.target))


class Download(unittest.TestCase):
    def fetch(self, served, size=None, sha=None):
        good = make_zip(NEW)
        rel = updater.Release("2.0.0", "https://example.invalid/u.zip",
                              sha or hashlib.sha256(good).hexdigest(), size or len(good))

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()
        before = set(os.listdir(tempfile.gettempdir()))
        try:
            return updater.download(rel, opener=lambda req, timeout: Resp(served))
        finally:
            self.leftover = {f for f in set(os.listdir(tempfile.gettempdir())) - before
                             if f.startswith("sk_update_")}

    def test_corrupted_or_incomplete_downloads_are_deleted(self):
        good = make_zip(NEW)
        for served in (good[:-10], good + b"x", good[:100] + b"\xff" + good[101:], b""):
            with self.assertRaises(ValueError):
                self.fetch(served)
            self.assertEqual(self.leftover, set())

    def test_connection_drop_mid_download(self):
        class Dropping(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()

            def read(self, n=-1):
                raise OSError("connection reset")
        rel = updater.Release("2.0.0", "https://example.invalid/u.zip", "0" * 64, 10)
        with self.assertRaises(OSError):
            updater.download(rel, opener=lambda req, timeout: Dropping())


class FailureRollsBack(Base):
    """A failure at any point of the install puts every file back."""

    def run_failing(self, fn_name, n, exc=OSError("disk full")):
        real = getattr(updater, fn_name)
        calls = {"n": 0}

        def failing(*a, **kw):
            calls["n"] += 1
            if calls["n"] == n:
                raise exc
            return real(*a, **kw)
        with mock.patch.object(updater, fn_name, failing):
            try:
                self.install()
                return None
            except updater.UpdateError as e:
                return e

    def test_failure_at_every_step_restores_everything(self):
        self.with_manifest()
        before = snapshot(self.target)
        for fn in ("_put", "_copy_complete", "_write_atomic", "_sha"):
            n = 1
            while True:
                err = self.run_failing(fn, n)
                if err is None:
                    break                          # past the last call: it installed
                self.assertEqual(snapshot(self.target), before, f"{fn} #{n}")
                self.assert_clean()
                self.assertTrue("put back as it was" in str(err) or
                                "Nothing in the program folder was changed" in str(err), str(err))
                self.assertFalse(err.installed_changed)
                n += 1
                write_tree(self.target, {})        # (same tree; loop continues)
            # reset to the old installation for the next function
            shutil.rmtree(self.target)
            os.makedirs(self.target)
            write_tree(self.target, OLD)
            write_tree(self.target, USER)
            self.with_manifest()
            self.assertGreater(n, 1, fn)

    def test_a_removed_file_that_is_in_use_is_left_in_place(self):
        self.with_manifest()
        with mock.patch.object(updater, "_remove", side_effect=PermissionError("in use")):
            self.install()
        snap = snapshot(self.target)
        self.assertEqual(snap["puzzle_assistant/gone.py"], b"G = 1\n")       # left, harmless
        self.assertEqual(snap["puzzle_assistant/a.py"], b"A = 2\n")          # update done
        self.assert_clean()

    def test_a_file_that_does_not_verify_is_rolled_back(self):
        real = updater._put

        def wrong(src, dst):
            real(src, dst)
            if dst.endswith("b.py"):
                with open(dst, "w") as fh:
                    fh.write("tampered")
        before = snapshot(self.target)
        with mock.patch.object(updater, "_put", wrong):
            with self.assertRaises(updater.UpdateError) as cm:
                self.install()
        self.assertIn("did not install correctly", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)

    def test_failed_package_install_rolls_back_and_says_so(self):
        before = snapshot(self.target)

        def pip_fails(changed, target):
            raise updater.PackageError("pip exit code 1")
        with self.assertRaises(updater.UpdateError) as cm:
            self.install(make_zip(dict(NEW, **{"requirements.txt": "numpy>=2\n"})),
                         packages=pip_fails)
        self.assertEqual(snapshot(self.target), before)
        self.assertIn(".venv may have been updated", str(cm.exception))

    def test_previous_backup_survives_until_the_new_version_is_verified(self):
        prev = os.path.join(self.target, "_previous", "puzzle_assistant", "a.py")
        seen = []

        def packages(changed, target):       # the last step before commit
            seen.append(open(prev).read())
            raise updater.PackageError("no")
        with self.assertRaises(updater.UpdateError):
            self.install(packages=packages)
        self.assertEqual(seen, ["A = 0  # the backup of the update before\n"])
        self.assertEqual(open(prev).read(), "A = 0  # the backup of the update before\n")

    def test_ctrl_c_during_install_also_rolls_back(self):
        self.with_manifest()
        before = snapshot(self.target)
        real, hit = updater._remove, []

        def remove(path):
            if not hit:
                hit.append(path)
                raise KeyboardInterrupt
            return real(path)
        with mock.patch.object(updater, "_remove", remove):
            with self.assertRaises(KeyboardInterrupt):
                self.install()
        self.assertEqual(snapshot(self.target), before)
        self.assert_clean()

    def test_a_rollback_that_cannot_finish_says_so_and_recover_finishes_it(self):
        self.with_manifest()
        before = snapshot(self.target)
        real_put = updater._put
        state = {"puts": 0, "failed": False}

        def put(src, dst):
            if updater.BACKUP in src and not state["failed"]:   # first restore fails
                state["failed"] = True
                raise PermissionError("locked")
            state["puts"] += 1
            if state["puts"] == 4:
                raise OSError("disk full")
            return real_put(src, dst)
        with mock.patch.object(updater, "_put", put):
            with self.assertRaises(updater.UpdateError) as cm:
                self.install()
        self.assertTrue(cm.exception.installed_changed)
        self.assertIn("could not be fully undone", str(cm.exception))
        self.assertTrue(os.path.exists(os.path.join(self.target, updater.JOURNAL)))
        note = updater.recover(self.target)
        self.assertIn("previous version was restored", note)
        self.assertEqual(snapshot(self.target), before)
        self.assert_clean()


KILLER = textwrap.dedent("""
    import os, shutil, sys
    sys.path.insert(0, sys.argv[1])
    from puzzle_assistant import updater
    target, zip_path, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
    count = [0]

    def wrap(mod, name):
        real = getattr(mod, name)
        def f(*a, **kw):
            count[0] += 1
            if count[0] == n:
                os._exit(9)                       # the power goes off
            return real(*a, **kw)
        setattr(mod, name, f)
    for mod, name in ((os, "replace"), (os, "remove"), (os, "rmdir"), (os, "makedirs"),
                      (os, "fsync"), (shutil, "copy2"), (shutil, "copyfile"),
                      (shutil, "copystat"), (shutil, "rmtree")):
        wrap(mod, name)
    updater.install(zip_path, target, packages=lambda c, t: None)
    print("FINISHED", count[0])
""")


class Interrupted(Base):
    """The process is killed before every single file-system operation of an
    update. Whatever the moment, the next start (recover) leaves exactly the
    old installation or exactly the new one - never a mix."""

    def fresh(self, where):
        if os.path.exists(where):
            shutil.rmtree(where)
        os.makedirs(where)
        write_tree(where, OLD)
        write_tree(where, USER)
        write_tree(where, {updater.MANIFEST: json.dumps(manifest_for(OLD))})

    def test_killed_at_every_step(self):
        script = os.path.join(self.tmp, "killer.py")
        with open(script, "w") as fh:
            fh.write(KILLER)
        target = os.path.join(self.tmp, "inst")
        self.fresh(target)
        old = snapshot(target)
        updater.install(self.zip_file(), target, packages=lambda c, t: None)
        new = snapshot(target)
        self.assertNotEqual(old, new)
        n, outcomes = 1, {"old": 0, "new": 0}
        while True:
            self.fresh(target)
            z = self.zip_file()
            run = subprocess.run([sys.executable, script, ROOT, target, z, str(n)],
                                 capture_output=True, text=True, timeout=60)
            if run.returncode == 0:
                self.assertIn("FINISHED", run.stdout)
                break
            self.assertEqual(run.returncode, 9, run.stderr)
            note = updater.recover(target)
            snap = snapshot(target)
            if snap == old:
                outcomes["old"] += 1
            else:
                self.assertEqual(snap, new, f"killed before operation {n}: {note}")
                outcomes["new"] += 1
            self.assertFalse(os.path.exists(os.path.join(target, updater.JOURNAL)))
            self.assertFalse(os.path.exists(os.path.join(target, updater.STAGING)))
            self.assertFalse(os.path.exists(os.path.join(target, updater.BACKUP)))
            n += 1
        self.assertGreater(outcomes["old"], 20)
        self.assertGreater(outcomes["new"], 0)


class Recovery(Base):
    def test_nothing_to_do(self):
        before = snapshot(self.target)
        self.assertIsNone(updater.recover(self.target))
        self.assertEqual(snapshot(self.target), before)

    def test_does_not_interfere_with_an_update_in_progress(self):
        write_tree(self.target, {updater.JOURNAL: json.dumps(
            {"format": 1, "state": "installing", "version": "2", "entries": [["my_notes.txt", False]]})})
        lock = updater._Lock(self.target)
        self.assertTrue(lock.acquire())
        try:
            self.assertIsNone(updater.recover(self.target))
            self.assertTrue(os.path.exists(os.path.join(self.target, "my_notes.txt")))
        finally:
            lock.release()

    def test_unreadable_or_unknown_journal_changes_nothing_and_says_so(self):
        for text in ("{not json", json.dumps({"format": 99, "state": "installing",
                                               "entries": [["my_notes.txt", False]]})):
            write_tree(self.target, {updater.JOURNAL: text})
            before = snapshot(self.target)
            note = updater.recover(self.target)
            self.assertIn("could not be undone automatically", note)
            self.assertEqual(snapshot(self.target), before)
            # and no new update is started on top of it
            with self.assertRaises(updater.UpdateError) as cm:
                self.install()
            self.assertTrue(cm.exception.installed_changed)
            self.assertEqual(snapshot(self.target), before)

    def test_journal_entries_cannot_reach_protected_or_outside_files(self):
        write_tree(self.tmp, {"outside.txt": "outside"})
        write_tree(self.target, {updater.JOURNAL: json.dumps(
            {"format": 1, "state": "installing", "version": "2",
             "entries": [["config.json", False], ["../outside.txt", False],
                         ["data/memory.db", False], [".venv/Scripts/python.exe", False]]})})
        updater.recover(self.target)
        snap = snapshot(self.target)
        self.assertEqual(snap["config.json"], b'{"mine": true}')
        self.assertEqual(snap["data/memory.db"], b"my memory")
        self.assertIn(".venv/Scripts/python.exe", snap)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "outside.txt")))

    def test_recover_at_start_never_raises(self):
        with mock.patch.object(updater, "recover", side_effect=RuntimeError("boom")):
            updater.recover_at_start()
        self.assertIn("could not be checked", updater.LAST_RECOVERY)
        updater.LAST_RECOVERY = None


class Packages(unittest.TestCase):
    def test_pip_failure_is_reported(self):
        with mock.patch.object(updater.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(updater.PackageError):
                updater.update_packages(["requirements.txt"], tempfile.gettempdir())
        with mock.patch.object(updater.subprocess, "run") as run:
            updater.update_packages(["app.py"], tempfile.gettempdir())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
