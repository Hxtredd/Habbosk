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
        for leftover in (updater.STAGING, updater.BACKUP, updater.JOURNAL,
                         updater.JOURNAL + ".tmp"):
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
            self.assertIn("another SK Assistant window", updater.recover(self.target))
            self.assertTrue(os.path.exists(os.path.join(self.target, "my_notes.txt")))
            self.assertTrue(os.path.exists(os.path.join(self.target, updater.JOURNAL)))
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



LOCK_STRESS = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    from puzzle_assistant import updater
    target, rounds, who = sys.argv[2], int(sys.argv[3]), sys.argv[4]
    marker = os.path.join(target, "inside")
    got = bad = 0
    for i in range(rounds):
        lock = updater._Lock(target)
        if lock.acquire():
            got += 1
            try:                                  # nobody else may be inside now
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                time.sleep(0.0005)
                os.remove(marker)
            except FileExistsError:
                bad += 1
            lock.release()
    print(who, got, bad)
""")

CONCURRENT_INSTALL = textwrap.dedent("""
    import sys
    sys.path.insert(0, sys.argv[1])
    from puzzle_assistant import updater
    try:
        updater.install(sys.argv[3], sys.argv[2], packages=lambda c, t: None)
        print("INSTALLED")
    except updater.UpdateError as e:
        print("REFUSED", e)
""")


class Concurrency(Base):
    def test_lock_file_is_permanent(self):
        lock = updater._Lock(self.target)
        self.assertTrue(lock.acquire())
        lock.release()
        self.assertTrue(os.path.exists(lock.path))
        self.install()
        self.assertTrue(os.path.exists(lock.path))
        updater.recover(self.target)
        self.assertTrue(os.path.exists(lock.path))

    def test_release_and_relock_race_has_one_winner(self):
        a, b, c = (updater._Lock(self.target) for _ in range(3))
        self.assertTrue(a.acquire())
        b._open()                        # b has the file open, waiting to lock it
        a.release()
        c_got = c.acquire()              # a newcomer
        b_got = b._try_lock()
        self.assertEqual(sorted([b_got, c_got]), [False, True])
        for lk in (b, c):
            lk.release()

    def test_a_lock_file_deleted_by_hand_cannot_give_two_holders(self):
        b, c = updater._Lock(self.target), updater._Lock(self.target)
        b._open()
        try:
            os.remove(b.path)            # e.g. someone tidies the folder by hand
        except PermissionError:          # Windows refuses to delete an open file
            b.release()
            self.skipTest("this system does not delete open files")
        self.assertTrue(c.acquire())     # locks a new file at the same path
        self.assertFalse(b._try_lock())  # the old, deleted file no longer counts
        c.release()

    def test_many_processes_never_hold_the_lock_together(self):
        script = os.path.join(self.tmp, "stress.py")
        with open(script, "w") as fh:
            fh.write(LOCK_STRESS)
        procs = [subprocess.Popen([sys.executable, script, ROOT, self.target, "300", str(i)],
                                  stdout=subprocess.PIPE, text=True) for i in range(4)]
        results = [p.communicate(timeout=120)[0].split() for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs))
        self.assertEqual([int(r[2]) for r in results], [0, 0, 0, 0])       # never two inside
        self.assertGreater(sum(int(r[1]) for r in results), 50)

    def test_concurrent_installs_and_recovery_never_mix(self):
        self.with_manifest()
        script = os.path.join(self.tmp, "inst.py")
        with open(script, "w") as fh:
            fh.write(CONCURRENT_INSTALL)
        clean = os.path.join(self.tmp, "reference")
        shutil.copytree(self.target, clean)
        updater.install(self.zip_file(name="ref.zip"), clean, packages=lambda c, t: None)
        expected = snapshot(clean)
        for _ in range(3):
            zips = [self.zip_file(name=f"c{i}.zip") for i in range(3)]
            procs = [subprocess.Popen([sys.executable, script, ROOT, self.target, z],
                                      stdout=subprocess.PIPE, text=True) for z in zips]
            notes = [updater.recover(self.target) for _ in range(20)]   # meanwhile, starts
            outs = [p.communicate(timeout=120)[0] for p in procs]
            self.assertTrue(all(o.startswith(("INSTALLED", "REFUSED")) for o in outs), outs)
            self.assertTrue(any(o.startswith("INSTALLED") for o in outs) or
                            os.path.exists(os.path.join(self.target, updater.MANIFEST)))
            for o in outs:
                if o.startswith("REFUSED"):
                    self.assertIn("already running", o)
            self.assertTrue(all(n in (None, updater._BUSY) for n in notes), notes)
            snap = snapshot(self.target)
            self.assertEqual({k: v for k, v in snap.items() if not k.startswith("_previous/")},
                             {k: v for k, v in expected.items() if not k.startswith("_previous/")})
            self.assert_clean()


APP_STUB_MAIN = "import os\ndef main():\n    open(os.path.join(os.path.dirname(os.path.dirname(" \
    "os.path.abspath(__file__))), 'STARTED'), 'w').close()\n"


class Startup(Base):
    """SK does not run on possibly mixed program files: if an interrupted
    update cannot be undone, it explains and stops, keeping everything
    needed for another attempt."""

    def interrupted(self):
        """An install killed half-way: journal 'installing', backup present."""
        self.with_manifest()
        self.before = snapshot(self.target)
        real, n = updater._put, {"i": 0}

        def put(src, dst):
            n["i"] += 1
            if n["i"] == 4:
                raise SystemExit("power off")      # not caught as a failure: no rollback
            return real(src, dst)
        with mock.patch.object(updater, "_put", put), \
                mock.patch.object(updater, "_rollback", return_value=False):
            with self.assertRaises(updater.UpdateError):
                self.install()
        self.assertEqual(updater._journal_state(self.target), "installing")
        self.assertNotEqual(snapshot(self.target), self.before)

    def recovery_files(self):
        return {k: v for k, v in snapshot(self.target).items()
                if k == updater.JOURNAL or k.startswith(updater.BACKUP + "/")}

    def test_nothing_to_do_starts(self):
        self.assertIsNone(updater.startup_check(self.target))

    def test_interrupted_update_is_undone_then_starts(self):
        self.interrupted()
        self.assertIsNone(updater.startup_check(self.target))
        self.assertEqual(snapshot(self.target), self.before)
        self.assertIn("previous version was restored", updater.LAST_RECOVERY)
        updater.LAST_RECOVERY = None

    def test_recovery_that_cannot_finish_blocks_and_keeps_everything(self):
        self.interrupted()
        kept = self.recovery_files()
        self.assertTrue(any(k.startswith(updater.BACKUP + "/") for k in kept))
        with mock.patch.object(updater, "_put", side_effect=PermissionError("locked")):
            msg = updater.startup_check(self.target)
        self.assertIn("cannot start yet", msg)
        self.assertIn(updater.JOURNAL, msg)
        self.assertEqual(self.recovery_files(), kept)             # nothing discarded
        # once the cause is gone, the next start restores and starts
        self.assertIsNone(updater.startup_check(self.target))
        self.assertEqual(snapshot(self.target), self.before)
        updater.LAST_RECOVERY = None

    def test_a_fault_in_recovery_itself_blocks(self):
        self.interrupted()
        kept = self.recovery_files()
        with mock.patch.object(updater, "_rollback", side_effect=RuntimeError("bug")):
            msg = updater.startup_check(self.target)
        self.assertIn("RuntimeError", msg)
        self.assertEqual(self.recovery_files(), kept)
        updater.LAST_RECOVERY = None

    def test_unreadable_journal_blocks(self):
        write_tree(self.target, {updater.JOURNAL: "{broken"})
        before = snapshot(self.target)
        self.assertIn("cannot start yet", updater.startup_check(self.target))
        self.assertEqual(snapshot(self.target), before)
        updater.LAST_RECOVERY = None

    def test_update_running_in_another_window_blocks(self):
        self.interrupted()
        lock = updater._Lock(self.target)
        self.assertTrue(lock.acquire())
        try:
            self.assertIn("another SK Assistant window", updater.startup_check(self.target))
            self.assertEqual(updater._journal_state(self.target), "installing")
        finally:
            lock.release()
        updater.LAST_RECOVERY = None

    def test_a_complete_update_that_could_not_tidy_up_still_starts(self):
        real = updater._finish_commit
        with mock.patch.object(updater, "_finish_commit", side_effect=OSError("busy")):
            self.install()
        self.assertEqual(updater._journal_state(self.target), "committed")
        with mock.patch.object(updater, "_finish_commit", side_effect=OSError("busy")):
            self.assertIsNone(updater.startup_check(self.target))     # files are complete
        self.assertIsNone(updater.startup_check(self.target))
        self.assertIsNone(updater._journal_state(self.target))
        self.assertTrue(callable(real))
        updater.LAST_RECOVERY = None

    # -- app.py itself, in a separate process -----------------------------
    def app_install(self):
        app = os.path.join(self.tmp, "app")
        os.makedirs(os.path.join(app, "puzzle_assistant"))
        shutil.copy(os.path.join(ROOT, "app.py"), app)
        for f in ("__init__.py", "config.py", "updater.py"):
            shutil.copy(os.path.join(ROOT, "puzzle_assistant", f),
                        os.path.join(app, "puzzle_assistant", f))
        with open(os.path.join(app, "puzzle_assistant", "main.py"), "w") as fh:
            fh.write(APP_STUB_MAIN)       # stands in for SK: records that it started
        return app

    def run_app(self, app):
        env = dict(os.environ, SK_ASSISTANT_NO_DIALOG="1")
        return subprocess.run([sys.executable, os.path.join(app, "app.py")], cwd=app, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_app_starts_normally(self):
        app = self.app_install()
        run = self.run_app(app)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(os.path.exists(os.path.join(app, "STARTED")))

    def test_app_refuses_to_start_when_recovery_cannot_finish(self):
        app = self.app_install()
        journal = json.dumps({"format": 99, "state": "installing", "entries": []})
        write_tree(app, {updater.JOURNAL: journal, updater.BACKUP + "/x.py": "old"})
        run = self.run_app(app)
        self.assertEqual(run.returncode, 3)
        self.assertIn("cannot start yet", run.stderr)
        self.assertFalse(os.path.exists(os.path.join(app, "STARTED")))
        self.assertEqual(open(os.path.join(app, updater.JOURNAL)).read(), journal)
        self.assertTrue(os.path.exists(os.path.join(app, updater.BACKUP, "x.py")))

    def test_app_refuses_when_the_update_code_cannot_load_and_a_journal_exists(self):
        app = self.app_install()
        with open(os.path.join(app, "puzzle_assistant", "updater.py"), "w") as fh:
            fh.write("def (:\n")
        write_tree(app, {updater.JOURNAL: "{}"})
        run = self.run_app(app)
        self.assertEqual(run.returncode, 3)
        self.assertIn("could not be loaded", run.stderr)
        self.assertFalse(os.path.exists(os.path.join(app, "STARTED")))
        self.assertTrue(os.path.exists(os.path.join(app, updater.JOURNAL)))
        os.remove(os.path.join(app, updater.JOURNAL))           # no update pending:
        self.assertEqual(self.run_app(app).returncode, 0)        # starts as before
        self.assertTrue(os.path.exists(os.path.join(app, "STARTED")))

    def test_app_undoes_an_interrupted_update_and_starts(self):
        self.interrupted()
        app = self.app_install()
        shutil.rmtree(app)
        shutil.copytree(self.target, app)
        for f in ("__init__.py", "config.py", "updater.py"):     # real code, stub SK
            shutil.copy(os.path.join(ROOT, "puzzle_assistant", f),
                        os.path.join(app, "puzzle_assistant", f))
        shutil.copy(os.path.join(ROOT, "app.py"), app)
        with open(os.path.join(app, "puzzle_assistant", "main.py"), "w") as fh:
            fh.write(APP_STUB_MAIN)
        run = self.run_app(app)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(os.path.exists(os.path.join(app, "STARTED")))
        self.assertFalse(os.path.exists(os.path.join(app, updater.JOURNAL)))
        self.assertFalse(os.path.exists(os.path.join(app, updater.BACKUP)))


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
