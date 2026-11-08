"""Windows behaviour of the updater.

Two kinds of tests:
  * SIMULATED (run everywhere): a fake `msvcrt` with Windows' byte-range
    lock rules, and file-system rules like Windows' (a file another program
    has open cannot be replaced or deleted, though it can usually be
    written in place; an exclusively opened one cannot be touched at all).
    These check the updater's own logic for those situations.
  * REAL WINDOWS (skipped elsewhere): the same situations with the real
    msvcrt, real sharing violations and real processes. They run on Windows
    in INSTALL.bat's self-test, or with
        .venv\\Scripts\\python.exe -m unittest tests.test_updater_windows -v

Everything happens in temporary folders.
"""
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from puzzle_assistant import updater  # noqa: E402
from test_updater_transactions import (NEW, OLD, USER, Base, make_zip, manifest_for,  # noqa: E402
                                       snapshot, write_tree)

WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# simulated Windows
# ---------------------------------------------------------------------------
class FakeMsvcrt(types.ModuleType):
    """msvcrt.locking with Windows' rules: a byte range locked through one
    handle cannot be locked through another (or again through the same one),
    unlocking needs the same range, closing a handle releases its locks."""
    LK_UNLCK, LK_LOCK, LK_NBLCK, LK_RLCK, LK_NBRLCK = 0, 1, 2, 3, 4

    def __init__(self):
        super().__init__("msvcrt")
        self.held = {}          # (dev, ino, offset) -> fd
        self.calls = []

    def _key(self, fd, nbytes):
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino, os.lseek(fd, 0, os.SEEK_CUR), nbytes)

    def locking(self, fd, mode, nbytes):
        key = self._key(fd, nbytes)
        self.calls.append((mode, key[2]))
        holder = self.held.get(key)
        if holder is not None:
            try:
                os.fstat(holder)
            except OSError:      # that handle was closed: Windows released it
                holder = None
                del self.held[key]
        if mode == self.LK_NBLCK:
            if holder is not None:
                raise OSError(errno.EACCES, "Permission denied")   # what Windows reports
            self.held[key] = fd
        elif mode == self.LK_UNLCK:
            if holder != fd:
                raise OSError(errno.EACCES, "Permission denied")   # not locked here
            del self.held[key]
        else:
            raise AssertionError("the updater must never wait for the lock")


class SimulatedWindows:
    """Patches the updater to behave as on Windows: msvcrt locks, and files
    that are 'open' in another program (`shared`: cannot be replaced or
    deleted, can be written in place) or 'exclusively open' (`exclusive`:
    cannot be touched at all)."""

    def __init__(self, shared=(), exclusive=()):
        self.shared = {os.path.abspath(p) for p in shared}
        self.exclusive = {os.path.abspath(p) for p in exclusive}
        self.msvcrt = FakeMsvcrt()
        self.replace_refused = []
        self.patches = []

    def _check(self, path, write=False, delete=False):
        p = os.path.abspath(path)
        if p in self.exclusive or (delete and p in self.shared):
            raise PermissionError(errno.EACCES, "The process cannot access the file because "
                                  "it is being used by another process", path)

    def __enter__(self):
        real_replace, real_remove = os.replace, os.remove
        real_copyfile, real_copystat, real_copy2 = shutil.copyfile, shutil.copystat, shutil.copy2
        real_open = open

        def replace(src, dst):
            try:
                self._check(dst, delete=True)
            except PermissionError:
                self.replace_refused.append(os.path.abspath(dst))
                raise
            self._check(src, delete=True)
            return real_replace(src, dst)

        def remove(path):
            self._check(path, delete=True)
            return real_remove(path)

        def copyfile(src, dst, *a, **kw):
            self._check(src)
            self._check(dst, write=True)
            return real_copyfile(src, dst, *a, **kw)

        def copy2(src, dst, *a, **kw):
            self._check(src)
            self._check(dst, write=True)
            return real_copy2(src, dst, *a, **kw)

        def copystat(src, dst, *a, **kw):
            self._check(dst, write=True)
            return real_copystat(src, dst, *a, **kw)

        def sha(path):
            self._check(path)
            return SimulatedWindows._real_sha(path)
        SimulatedWindows._real_sha = updater._sha
        fake_sys = types.SimpleNamespace(platform="win32", executable=sys.executable)
        self.patches = [
            mock.patch.object(updater, "sys", fake_sys),
            mock.patch.dict(sys.modules, {"msvcrt": self.msvcrt}),
            mock.patch.object(updater.os, "replace", replace),
            mock.patch.object(updater.os, "remove", remove),
            mock.patch.object(updater.shutil, "copyfile", copyfile),
            mock.patch.object(updater.shutil, "copy2", copy2),
            mock.patch.object(updater.shutil, "copystat", copystat),
            mock.patch.object(updater, "_sha", sha),
            mock.patch.object(updater.time, "sleep"),
        ]
        for p in self.patches:
            p.start()
        self._open = real_open
        return self

    def __exit__(self, *a):
        for p in reversed(self.patches):
            p.stop()


class SimulatedMsvcrtLock(Base):
    def test_lock_unlock_and_second_holder(self):
        with SimulatedWindows() as win:
            a, b = updater._Lock(self.target), updater._Lock(self.target)
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())             # EACCES from msvcrt -> refused
            a.release()
            self.assertTrue(b.acquire())
            b.release()
            self.assertEqual(win.msvcrt.held, {})
            # always byte 0, never a waiting mode
            self.assertTrue(all(off == 0 for _, off in win.msvcrt.calls))
            self.assertEqual({m for m, _ in win.msvcrt.calls},
                             {FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK})
        self.assertTrue(os.path.exists(os.path.join(self.target, updater.LOCK)))

    def test_unlock_hits_the_locked_byte_even_after_the_position_moved(self):
        with SimulatedWindows() as win:
            a = updater._Lock(self.target)
            self.assertTrue(a.acquire())
            os.write(a.fd, b"x")                      # file position is now 1
            a.release()
            self.assertEqual(win.msvcrt.held, {})     # unlocked, not just closed

    def test_install_and_recover_respect_the_lock(self):
        with SimulatedWindows():
            other = updater._Lock(self.target)
            self.assertTrue(other.acquire())
            with self.assertRaises(updater.UpdateError) as cm:
                self.install()
            self.assertIn("already running", str(cm.exception))
            write_tree(self.target, {updater.JOURNAL: json.dumps(
                {"format": 1, "state": "installing", "version": "2", "entries": []})})
            self.assertEqual(updater.recover(self.target), updater._BUSY)
            self.assertIn("another SK Assistant window", updater.startup_check(self.target))
            other.release()
            self.assertIsNone(updater.startup_check(self.target))
            self.install()
        self.assertEqual(snapshot(self.target)["puzzle_assistant/a.py"], b"A = 2\n")
        updater.LAST_RECOVERY = None


class SimulatedFileLocks(Base):
    def p(self, rel):
        return os.path.join(self.target, *rel.split("/"))

    def test_replace_refused_falls_back_to_writing_in_place(self):
        shared = [self.p("assets/logo.bin"), self.p("puzzle_assistant/a.py")]
        with SimulatedWindows(shared=shared) as win:
            self.install()
        self.assertEqual(sorted(set(win.replace_refused)), sorted(shared))
        snap = snapshot(self.target)
        self.assertEqual(snap["assets/logo.bin"], b"\x00\x01new")
        self.assertEqual(snap["_previous/assets/logo.bin"], b"\x00\x01old")
        self.assertEqual(snap["puzzle_assistant/a.py"], b"A = 2\n")
        self.assert_clean()

    def test_a_removed_file_that_is_open_stays(self):
        self.with_manifest()
        with SimulatedWindows(shared=[self.p("puzzle_assistant/gone.py")]):
            self.install()
        snap = snapshot(self.target)
        self.assertEqual(snap["puzzle_assistant/gone.py"], b"G = 1\n")
        self.assertEqual(snap["puzzle_assistant/a.py"], b"A = 2\n")
        self.assert_clean()

    def test_exclusively_open_file_fails_the_update_and_everything_is_put_back(self):
        self.with_manifest()
        before = snapshot(self.target)
        with SimulatedWindows(exclusive=[self.p("assets/logo.bin")]):
            with self.assertRaises(updater.UpdateError) as cm:
                self.install()
        # it cannot even be read, so the update stops before changing anything
        self.assertIn("Nothing in the program folder was changed", str(cm.exception))
        self.assertFalse(cm.exception.installed_changed)
        self.assertEqual(snapshot(self.target), before)
        self.assert_clean()

    def test_rollback_blocked_by_a_lock_keeps_everything_and_finishes_later(self):
        self.with_manifest()
        before = snapshot(self.target)
        real_put, state = updater._put, {"n": 0}

        def put(src, dst):          # an update fails half-way...
            state["n"] += 1
            if state["n"] == 5:
                raise OSError("disk full")
            return real_put(src, dst)
        target_a = self.p("puzzle_assistant/a.py")
        with SimulatedWindows() as win, mock.patch.object(updater, "_put", put):
            real_restore = updater._rollback

            def rollback(target, entries):   # ...and a scanner opens a.py meanwhile
                win.exclusive.add(os.path.abspath(target_a))
                return real_restore(target, entries)
            with mock.patch.object(updater, "_rollback", rollback):
                with self.assertRaises(updater.UpdateError) as cm:
                    self.install()
            self.assertTrue(cm.exception.installed_changed)
            self.assertIn("could not be fully undone", str(cm.exception))
            journal = open(os.path.join(self.target, updater.JOURNAL)).read()
            self.assertIn("cannot start yet", updater.startup_check(self.target))
            self.assertEqual(open(os.path.join(self.target, updater.JOURNAL)).read(), journal)
            self.assertTrue(os.path.isdir(os.path.join(self.target, updater.BACKUP)))
            win.exclusive.clear()                   # the scanner lets go
            self.assertIsNone(updater.startup_check(self.target))
        self.assertEqual(snapshot(self.target), before)
        self.assert_clean()
        updater.LAST_RECOVERY = None


KILL_IN_FALLBACK = textwrap.dedent("""
    import os, shutil, sys
    sys.path.insert(0, sys.argv[1])
    from puzzle_assistant import updater
    target, zip_path, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
    real_replace, real_copyfile = os.replace, shutil.copyfile
    count = [0]

    def replace(src, dst):                 # every program file is 'open': no replacing
        if src.endswith(".sk_new"):
            raise PermissionError(13, "in use")
        return real_replace(src, dst)

    def copyfile(src, dst, *a, **kw):      # the in-place fallback copy
        in_place = dst.startswith(target) and updater.BACKUP not in dst and \\
            updater.STAGING not in dst and not dst.endswith((".sk_new", ".part", ".tmp"))
        if in_place:
            count[0] += 1
        if in_place and count[0] == n:
            data = open(src, "rb").read()
            with open(dst, "wb") as fh:    # half written...
                fh.write(data[:len(data) // 2])
            os._exit(9)                    # ...and the power goes off
        return real_copyfile(src, dst, *a, **kw)
    os.replace = replace
    shutil.copyfile = copyfile
    updater.time.sleep = lambda s: None
    updater.install(zip_path, target, packages=lambda c, t: None)
    print("FINISHED", count[0])
""")


class InterruptedFallbackCopy(Base):
    """Power cut in the middle of an in-place (fallback) copy, at every such
    copy; then recovery, both while the files are still 'open' and after."""

    def fresh(self, where):
        if os.path.exists(where):
            shutil.rmtree(where)
        os.makedirs(where)
        write_tree(where, OLD)
        write_tree(where, USER)
        write_tree(where, {updater.MANIFEST: json.dumps(manifest_for(OLD))})

    def test_killed_during_each_fallback_copy(self):
        script = os.path.join(self.tmp, "kill_fallback.py")
        with open(script, "w") as fh:
            fh.write(KILL_IN_FALLBACK)
        target = os.path.join(self.tmp, "inst")
        self.fresh(target)
        old = snapshot(target)
        killed, blocked = 0, []
        for still_open in (True, False):
            n = 1
            while True:
                self.fresh(target)
                z = os.path.join(self.tmp, "k.zip")
                with open(z, "wb") as fh:
                    fh.write(make_zip(NEW))
                run = subprocess.run([sys.executable, script, ROOT, target, z, str(n)],
                                     capture_output=True, text=True, timeout=60)
                if run.returncode == 0:
                    break
                self.assertEqual(run.returncode, 9, run.stderr)
                killed += 1
                self.assertNotEqual(snapshot(target), old)           # really half-written
                if still_open:
                    program = [full for rel, full in updater._walk(target).items()
                               if not rel.startswith(("_update", updater.MANIFEST))
                               and not rel.endswith((".sk_new", ".part", ".tmp"))]
                    new_files = {os.path.abspath(f) for f in program
                                 if os.path.relpath(f, target).replace(os.sep, "/") not in OLD
                                 and os.path.relpath(f, target).replace(os.sep, "/") not in USER}
                    with SimulatedWindows(shared=program):     # program files still open
                        msg = updater.startup_check(target)
                    if msg is not None:
                        # only a half-written NEW file that is still open (Windows cannot
                        # delete it) may hold recovery up; start-up is refused and the
                        # journal and backup are kept for the next start
                        blocked.append(n)
                        self.assertTrue(new_files, msg)
                        self.assertIn("cannot start yet", msg)
                        self.assertTrue(os.path.exists(os.path.join(target, updater.JOURNAL)))
                        self.assertTrue(os.path.isdir(os.path.join(target, updater.BACKUP)))
                        self.assertIsNone(updater.startup_check(target))   # released: done
                else:
                    self.assertIsNone(updater.startup_check(target))
                self.assertEqual(snapshot(target), old, f"copy {n}, files open: {still_open}")
                self.assertFalse(os.path.exists(os.path.join(target, updater.JOURNAL)))
                n += 1
        self.assertGreater(killed, 10)
        self.assertLess(len(blocked), killed)
        updater.LAST_RECOVERY = None


# ---------------------------------------------------------------------------
# real Windows (skipped elsewhere)
# ---------------------------------------------------------------------------
HOLD_LOCK = textwrap.dedent("""
    import sys
    sys.path.insert(0, sys.argv[1])
    from puzzle_assistant import updater
    lock = updater._Lock(sys.argv[2])
    print("LOCKED" if lock.acquire() else "BUSY", flush=True)
    sys.stdin.readline()
""")


@unittest.skipUnless(WINDOWS, "real Windows behaviour: runs only on Windows")
class RealWindows(Base):
    def p(self, rel):
        return os.path.join(self.target, *rel.split("/"))

    def hold_lock(self):
        script = os.path.join(self.tmp, "hold.py")
        with open(script, "w") as fh:
            fh.write(HOLD_LOCK)
        proc = subprocess.Popen([sys.executable, script, ROOT, self.target], text=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.assertEqual(proc.stdout.readline().strip(), "LOCKED")
        return proc

    def test_msvcrt_lock_between_processes_and_after_a_crash(self):
        proc = self.hold_lock()
        try:
            self.assertFalse(updater._Lock(self.target).acquire())
            with self.assertRaises(updater.UpdateError) as cm:
                self.install()
            self.assertIn("already running", str(cm.exception))
        finally:
            proc.kill()                           # a crash: Windows releases the lock
            proc.wait()
        lock = updater._Lock(self.target)
        self.assertTrue(lock.acquire())
        lock.release()
        self.assertTrue(os.path.exists(lock.path))

    def test_open_lock_file_cannot_be_deleted(self):
        lock = updater._Lock(self.target)
        self.assertTrue(lock.acquire())
        try:
            with self.assertRaises(PermissionError):
                os.remove(lock.path)
        finally:
            lock.release()

    def test_real_sharing_violation_uses_the_in_place_fallback(self):
        held = open(self.p("assets/logo.bin"), "rb")      # like a font in use
        try:
            probe = self.p("assets/logo.bin") + ".probe"
            with open(probe, "wb") as fh:
                fh.write(b"x")
            with self.assertRaises(PermissionError):          # Windows really refuses
                os.replace(probe, self.p("assets/logo.bin"))
            os.remove(probe)
            self.install()
        finally:
            held.close()
        snap = snapshot(self.target)
        self.assertEqual(snap["assets/logo.bin"], b"\x00\x01new")
        self.assertEqual(snap["_previous/assets/logo.bin"], b"\x00\x01old")
        self.assert_clean()

    def test_real_open_file_is_not_removed(self):
        self.with_manifest()
        held = open(self.p("puzzle_assistant/gone.py"), "rb")
        try:
            self.install()
        finally:
            held.close()
        self.assertEqual(snapshot(self.target)["puzzle_assistant/gone.py"], b"G = 1\n")
        self.assert_clean()

    def test_real_font_in_use_is_updated(self):
        try:
            from PIL import ImageFont
        except ImportError:
            self.skipTest("Pillow not installed")
        font_src = os.path.join(ROOT, "assets", "fonts", "Inter-Bold.otf")
        font_old = open(font_src, "rb").read()
        write_tree(self.target, {"assets/fonts/Inter-Bold.otf": font_old})
        f = ImageFont.truetype(self.p("assets/fonts/Inter-Bold.otf"), 12)   # FreeType keeps it open
        self.install(make_zip(dict(NEW, **{"assets/fonts/Inter-Bold.otf":
                                           font_old[:-1] + b"\x00"})))
        del f
        self.assertEqual(snapshot(self.target)["assets/fonts/Inter-Bold.otf"][-1:], b"\x00")
        self.assert_clean()

    def _exclusive(self, path):
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        h = k32.CreateFileW(path, 0x80000000, 0, None, 3, 0x80, None)   # share mode 0
        self.assertNotEqual(h, wintypes.HANDLE(-1).value)
        return lambda: k32.CloseHandle(h)

    def test_real_exclusive_lock_fails_safely(self):
        self.with_manifest()
        before = snapshot(self.target)
        close = self._exclusive(self.p("assets/logo.bin"))
        try:
            with self.assertRaises(updater.UpdateError):
                self.install()
        finally:
            close()
        self.assertIsNone(updater.startup_check(self.target))
        self.assertEqual(snapshot(self.target), before)
        updater.LAST_RECOVERY = None

    def test_real_rollback_blocked_then_finished_after_the_lock_is_gone(self):
        self.with_manifest()
        before = snapshot(self.target)
        real_put, state, closer = updater._put, {"n": 0}, []

        def put(src, dst):
            state["n"] += 1
            if state["n"] == 5:
                closer.append(self._exclusive(self.p("puzzle_assistant/a.py")))
                raise OSError("disk full")
            return real_put(src, dst)
        try:
            with mock.patch.object(updater, "_put", put):
                with self.assertRaises(updater.UpdateError) as cm:
                    self.install()
            self.assertTrue(cm.exception.installed_changed)
            self.assertIn("cannot start yet", updater.startup_check(self.target))
            self.assertTrue(os.path.exists(os.path.join(self.target, updater.JOURNAL)))
        finally:
            for c in closer:
                c()
        self.assertIsNone(updater.startup_check(self.target))
        self.assertEqual(snapshot(self.target), before)
        updater.LAST_RECOVERY = None


if __name__ == "__main__":
    unittest.main()
