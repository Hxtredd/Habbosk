"""Key-store hardening (audit findings F1, F2, F3 of the B6 Windows-fix review).

F1  two callers creating the same key at the same moment must end with ONE key: every
    caller returns the key that is stored (unique temporary files, no-overwrite
    publication, the loser discards its own key and loads the published one).
F2  a key must be exactly 32 bytes: damaged, empty, truncated or oversized key files are
    refused, and left byte-identical - a new key is never made over them.
F3  temporary files are unique and exclusively created, owner-only where the system
    supports it, never reused and never written through a symbolic link.

Linux / macOS and Windows differ only in how a key is published without overwriting (a
hard link, or - when hard links are unavailable - an exclusive create; on Windows a
rename that refuses an existing target). Everything here runs everywhere; where a route
exists only on another system it is EMULATED (and says so) - the Windows runs of these
tests on the owner's PC are the real check of the Windows route.
"""
import contextlib
import errno
import itertools
import multiprocessing
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from puzzle_assistant.brain import keystore  # noqa: E402

WINDOWS = sys.platform == "win32"
POSIX_ONLY = unittest.skipIf(WINDOWS, "permission bits / hard links: not a Windows concept")
KEY = bytes(range(32))          # holds LF (0x0A) and CR (0x0D) bytes on purpose


def plain(key):
    return keystore.MAGIC_PLAIN + key


def snapshot(folder):
    """name -> (bytes, mode, mtime_ns) for every file in a folder."""
    out = {}
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        st = os.lstat(p)
        with open(p, "rb") as fh:
            out[n] = (fh.read() if stat.S_ISREG(st.st_mode) else b"<not a file>",
                      stat.S_IMODE(st.st_mode), st.st_mtime_ns)
    return out


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sk_keystore_")
        self.folder = os.path.join(self.d, "keys")
        os.makedirs(self.folder)
        self.path = os.path.join(self.folder, "host.key")
        self.addCleanup(shutil.rmtree, self.d, True)
        # a damaged key file is refused only after a bounded wait (another caller may still
        # be writing it); the tests keep that wait short unless they test the waiting itself
        patcher = mock.patch.object(keystore, "WAIT_FOR_WINNER", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_key_file(self, blob, mode=0o600):
        with open(self.path, "wb") as fh:
            fh.write(blob)
        if not WINDOWS:
            os.chmod(self.path, mode)

    def stored_key(self):
        with open(self.path, "rb") as fh:
            return keystore.unprotect(fh.read())

    def only_the_key_file(self):
        self.assertEqual(os.listdir(self.folder), [os.path.basename(self.path)])

    @contextlib.contextmanager
    def saw_nothing_yet(self):
        """The caller's first existence check says 'no key yet' although another caller has
        meanwhile published one: the race, made deterministic."""
        real, first = os.path.exists, [True]

        def exists(p):
            if p == self.path and first[0]:
                first[0] = False
                return False
            return real(p)
        with mock.patch.object(os.path, "exists", exists):
            yield


@contextlib.contextmanager
def windows_like_rename():
    """EMULATION on Linux / macOS: a rename that refuses an existing target, atomically,
    as Windows does, and the key store told to publish that way."""
    real, lock = os.rename, threading.Lock()

    def rename(src, dst, *a, **k):
        with lock:
            if os.path.lexists(dst):
                raise FileExistsError(errno.EEXIST, "target exists", dst)
            return real(src, dst, *a, **k)
    with mock.patch.object(os, "rename", rename), \
            mock.patch.object(keystore, "_PUBLISH_BY_RENAME", True):
        yield


@contextlib.contextmanager
def no_hard_links():
    def link(*a, **k):
        raise OSError(errno.EPERM, "hard links are not supported here")
    with mock.patch.object(os, "link", link):
        yield


@contextlib.contextmanager
def gated(barrier, then=None):
    """Every way of publishing a key waits (once per thread) until all callers are there,
    so all of them have decided 'no key yet' and all of them publish together - the worst
    case, made deterministic. `then` may replace the publication (hard links unavailable)."""
    seen = threading.local()
    patches = []
    for name in ("link", "rename", "replace"):
        orig = getattr(os, name)

        def make(orig, name):
            def gate(*a, **k):
                if not getattr(seen, "done", False):
                    seen.done = True
                    barrier.wait(30)
                if then is not None and name == "link":
                    return then(*a, **k)
                return orig(*a, **k)
            return gate
        patches.append(mock.patch.object(os, name, make(orig, name)))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        yield


# =========================================================================== F2
class KeyLength(Tmp):
    def test_unprotect_accepts_exactly_32_bytes(self):
        self.assertEqual(keystore.unprotect(plain(KEY)), KEY)
        for n in (0, 1, 31, 33, 64, 4000):
            with self.subTest(length=n):
                with self.assertRaises(OSError):
                    keystore.unprotect(plain(bytes(range(256)) * 16)[:5 + n])

    def test_refused_files_stay_byte_identical_and_nothing_is_made(self):
        damaged_crlf = plain(KEY).replace(b"\n", b"\r\n")        # an editor / git autocrlf
        cases = {
            "empty file": b"",
            "magic only": keystore.MAGIC_PLAIN,
            "31 bytes": plain(KEY[:31]),
            "33 bytes": plain(KEY + b"\x00"),
            "CR LF damaged": damaged_crlf,
            "far too long": plain(KEY * 200),
            "wrong format": b"not an sk key file at all",
        }
        self.assertNotEqual(damaged_crlf, plain(KEY))
        for name, blob in cases.items():
            with self.subTest(name):
                self.write_key_file(blob)
                before = snapshot(self.folder)
                with self.assertRaises(OSError) as cm:
                    keystore.load_or_create_key(self.path)
                self.assertNotIn(KEY.hex(), str(cm.exception))        # never prints a key
                self.assertEqual(snapshot(self.folder), before)       # untouched, nothing new
                self.only_the_key_file()

    def test_an_error_names_the_problem(self):
        self.write_key_file(plain(KEY[:31]))
        with self.assertRaises(keystore.KeyFileDamaged) as cm:
            keystore.load_or_create_key(self.path)
        self.assertIn("31", str(cm.exception))
        self.assertIn("32", str(cm.exception))

    def test_a_key_of_exactly_32_bytes_loads(self):
        self.write_key_file(plain(KEY))
        self.assertEqual(keystore.load_or_create_key(self.path), KEY)
        self.only_the_key_file()

    def test_a_huge_file_is_not_read_into_memory(self):
        with open(self.path, "wb") as fh:
            fh.write(keystore.MAGIC_PLAIN)
            fh.truncate(32 * 1024 * 1024)                              # a sparse 32 MB file
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            with self.assertRaises(OSError):
                keystore.load_or_create_key(self.path)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 1024 * 1024, "the key file was read far past its size limit")
        self.assertEqual(os.path.getsize(self.path), 32 * 1024 * 1024)

    @unittest.skipUnless(WINDOWS, "real DPAPI")
    def test_windows_dpapi_keys_of_the_wrong_length_are_refused(self):
        for n in (0, 31, 33):
            with self.subTest(length=n):
                blob = keystore.MAGIC_DPAPI + keystore._dpapi(bytes(n), True)
                with self.assertRaises(OSError):
                    keystore.unprotect(blob)
        good = keystore.protect(KEY)
        self.assertEqual(keystore.unprotect(good), KEY)

    def test_a_published_file_that_is_damaged_is_refused_unchanged(self):
        """Lost the race to a caller whose file is damaged: refuse it, do not replace it."""
        self.write_key_file(plain(KEY[:31]))
        before = snapshot(self.folder)
        with mock.patch.object(keystore, "WAIT_FOR_WINNER", 0.2), self.saw_nothing_yet():
            with self.assertRaises(keystore.KeyFileDamaged):
                keystore.load_or_create_key(self.path)
        self.assertEqual(snapshot(self.folder), before)


# =========================================================================== F3
class TemporaryFiles(Tmp):
    @POSIX_ONLY
    def test_a_new_key_is_owner_only_even_with_a_permissive_umask(self):
        modes = []
        real_link = os.link

        def spy(src, dst, *a, **k):
            modes.append(stat.S_IMODE(os.stat(src).st_mode))          # the temporary file
            return real_link(src, dst, *a, **k)
        old = os.umask(0)
        try:
            with mock.patch.object(os, "link", spy):
                key = keystore.load_or_create_key(self.path)
        finally:
            os.umask(old)
        self.assertEqual(modes, [0o600])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(self.stored_key(), key)
        self.only_the_key_file()

    def test_a_stale_fixed_name_temporary_file_is_never_reused(self):
        """The old code wrote to '<key file>.tmp': a stale one (here loose, 0644) kept its mode."""
        stale = self.path + ".tmp"
        with open(stale, "wb") as fh:
            fh.write(b"stale junk from an earlier crash")
        if not WINDOWS:
            os.chmod(stale, 0o644)
        before = snapshot(self.folder)[os.path.basename(stale)]
        key = keystore.load_or_create_key(self.path)
        self.assertEqual(snapshot(self.folder)[os.path.basename(stale)], before)   # untouched
        self.assertEqual(self.stored_key(), key)
        if not WINDOWS:
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(self.folder)),
                         sorted([os.path.basename(self.path), os.path.basename(stale)]))

    @POSIX_ONLY
    def test_a_symbolic_link_at_the_old_temporary_name_is_not_followed(self):
        victim = os.path.join(self.d, "victim.txt")
        with open(victim, "wb") as fh:
            fh.write(b"do not touch")
        os.symlink(victim, self.path + ".tmp")
        key = keystore.load_or_create_key(self.path)
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), b"do not touch")
        self.assertEqual(self.stored_key(), key)
        self.assertTrue(os.path.islink(self.path + ".tmp"))

    def test_a_planted_temporary_name_is_never_written_through(self):
        """Even if an attacker could guess the next random name: the file is created
        exclusively, so a planted file or link there is skipped, not written through."""
        planted = os.path.join(self.folder, ".host.key.deadbeefdeadbeef.tmp")
        victim = os.path.join(self.d, "victim.txt")
        with open(victim, "wb") as fh:
            fh.write(b"do not touch")
        made_link = False
        if not WINDOWS:
            os.symlink(victim, planted)
            made_link = True
        else:
            with open(planted, "wb") as fh:
                fh.write(b"planted")
        names = iter(["deadbeefdeadbeef"])
        real = keystore.secrets.token_hex

        def token_hex(n=None):
            try:
                return next(names)
            except StopIteration:
                return real(n)
        with mock.patch.object(keystore.secrets, "token_hex", token_hex):
            key = keystore.load_or_create_key(self.path)
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), b"do not touch")
        self.assertEqual(self.stored_key(), key)
        self.assertTrue(os.path.islink(planted) if made_link else os.path.exists(planted))
        if not made_link:
            with open(planted, "rb") as fh:
                self.assertEqual(fh.read(), b"planted")

    def test_no_temporary_file_is_left_behind(self):
        # a created key
        keystore.load_or_create_key(self.path)
        self.only_the_key_file()
        # a lost race: the other caller's key stays, ours is discarded
        os.remove(self.path)
        self.write_key_file(plain(KEY))
        with self.saw_nothing_yet():
            self.assertEqual(keystore.load_or_create_key(self.path), KEY)
        self.only_the_key_file()
        # a failure while publishing: nothing is published, nothing is left
        os.remove(self.path)

        def boom(*a, **k):
            raise OSError(errno.EIO, "disk trouble")
        with mock.patch.object(os, "link", boom), mock.patch.object(os, "rename", boom):
            with self.assertRaises(OSError):
                keystore.load_or_create_key(self.path)
        self.assertEqual(os.listdir(self.folder), [])

    def test_a_failure_while_writing_leaves_no_file_at_all(self):
        with mock.patch.object(keystore, "protect", side_effect=RuntimeError("no key today")):
            with self.assertRaises(RuntimeError):
                keystore.load_or_create_key(self.path)
        self.assertEqual(os.listdir(self.folder), [])


# =========================================================================== F1
class NeverOverwrites(Tmp):
    def routes(self):
        out = [("default route", contextlib.nullcontext)]
        if not WINDOWS:
            out += [("no hard links", no_hard_links), ("windows rename (emulated)",
                                                         windows_like_rename)]
        return out

    def test_an_existing_key_is_never_overwritten_by_any_route(self):
        for name, ctx in self.routes():
            with self.subTest(name):
                shutil.rmtree(self.folder)
                os.makedirs(self.folder)
                other = os.urandom(32)
                self.write_key_file(keystore.protect(other))
                before = snapshot(self.folder)
                with ctx(), self.saw_nothing_yet():
                    self.assertEqual(keystore.load_or_create_key(self.path), other)
                self.assertEqual(snapshot(self.folder), before)       # not even its mtime
                self.only_the_key_file()

    def test_hard_links_unavailable_still_publishes_safely(self):
        if WINDOWS:
            self.skipTest("Windows publishes by rename, not by hard link")
        with no_hard_links(), mock.patch.object(keystore, "_publish_exclusive",
                                                wraps=keystore._publish_exclusive) as spy:
            key = keystore.load_or_create_key(self.path)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(self.stored_key(), key)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.only_the_key_file()

    @POSIX_ONLY
    def test_a_failure_while_filling_the_final_file_leaves_no_partial_key(self):
        """Hard links unavailable: the final file is created exclusively and then filled.
        If filling it fails, the half-made file (ours: we created it) must not stay."""
        real, calls = os.fsync, [0]

        def fsync(fd):
            calls[0] += 1
            if calls[0] == 2:                                          # the 2nd: the final file
                raise OSError(errno.EIO, "disk trouble")
            return real(fd)
        with no_hard_links(), mock.patch.object(os, "fsync", fsync):
            with self.assertRaises(OSError):
                keystore.load_or_create_key(self.path)
        self.assertEqual(calls[0], 2)
        self.assertEqual(os.listdir(self.folder), [])

    @POSIX_ONLY
    def test_any_other_link_failure_is_reported_not_hidden(self):
        def boom(*a, **k):
            raise OSError(errno.EIO, "disk trouble")
        with mock.patch.object(os, "link", boom):
            with self.assertRaises(OSError):
                keystore.load_or_create_key(self.path)
        self.assertEqual(os.listdir(self.folder), [])

    @POSIX_ONLY
    def test_the_windows_route_works_when_emulated(self):
        with windows_like_rename():
            key = keystore.load_or_create_key(self.path)
            self.assertEqual(keystore.load_or_create_key(self.path), key)
        self.assertEqual(self.stored_key(), key)
        self.only_the_key_file()

    def test_a_half_written_published_key_is_waited_for(self):
        """Where hard links are unavailable the winner creates the final file and then fills
        it: the loser must wait for the complete key, not return its own or give up."""
        good = os.urandom(32)
        self.write_key_file(plain(good)[:10])                          # half written
        blob = keystore.protect(good)

        def finish():
            time.sleep(0.3)
            with open(self.path, "wb") as fh:
                fh.write(blob)
        t = threading.Thread(target=finish)
        t.start()
        try:
            with mock.patch.object(keystore, "WAIT_FOR_WINNER", 5.0), self.saw_nothing_yet():
                self.assertEqual(keystore.load_or_create_key(self.path), good)
        finally:
            t.join()
        self.only_the_key_file()

    @unittest.skipUnless(WINDOWS, "the real Windows rename")
    def test_real_windows_rename_refuses_an_existing_key(self):
        other = os.urandom(32)
        self.write_key_file(keystore.protect(other))
        tmp = os.path.join(self.folder, "pending.tmp")
        with open(tmp, "wb") as fh:
            fh.write(b"a different key's bytes")
        before = snapshot(self.folder)
        self.assertIs(keystore._publish(tmp, self.path), False)
        after = snapshot(self.folder)
        self.assertEqual(after[os.path.basename(self.path)], before[os.path.basename(self.path)])
        self.assertTrue(os.path.exists(tmp))                           # the caller removes it


class ArrivingDuringPublication(Tmp):
    """Review finding on 9196ef1: with hard links unavailable the winner creates the final
    file and only THEN fills it. A caller arriving in that interval used to read the empty
    or half-written file at once and fail. It must wait for the key and return the same
    key; a key that never completes (a crash) must be refused after a bounded wait,
    unchanged; a genuinely damaged key is never replaced."""

    def pause_after_creating_the_final_file(self):
        """The publisher stops right after it created the (still empty) final file."""
        created, proceed = threading.Event(), threading.Event()
        real = keystore._create_exclusive

        def create(p):
            fd = real(p)
            if p == self.path:
                created.set()
                if not proceed.wait(30):
                    raise RuntimeError("the test did not release the publisher")
            return fd
        return created, proceed, create

    def caller(self, out, name):
        def run():
            try:
                out[name] = keystore.load_or_create_key(self.path)
            except BaseException as exc:                              # noqa: BLE001
                out[name] = exc
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    @POSIX_ONLY
    def test_a_caller_arriving_during_publication_waits_and_gets_the_same_key(self):
        created, proceed, create = self.pause_after_creating_the_final_file()
        out = {}
        with no_hard_links(), mock.patch.object(keystore, "_create_exclusive", create), \
                mock.patch.object(keystore, "WAIT_FOR_WINNER", 10.0):
            a = self.caller(out, "a")
            self.assertTrue(created.wait(10), "the publisher never created the final file")
            self.assertEqual(os.path.getsize(self.path), 0)            # exists, not yet filled
            b = self.caller(out, "b")
            b.join(0.7)
            self.assertTrue(b.is_alive(), "the second caller did not wait for the key: %r"
                            % (out.get("b"),))
            self.assertNotIn("b", out)
            proceed.set()
            a.join(30)
            b.join(30)
        self.assertIsInstance(out["a"], bytes)
        self.assertEqual(out["a"], out["b"], "the two callers ended with different keys")
        self.assertEqual(out["a"], self.stored_key())
        self.only_the_key_file()

    @POSIX_ONLY
    def test_a_waiting_caller_recovers_when_the_publisher_fails(self):
        """The publisher fails while filling the file and removes it: a caller waiting for
        it must not crash - it starts over and creates the key itself."""
        created, proceed, create = self.pause_after_creating_the_final_file()
        real_fsync, out, publisher = os.fsync, {}, []

        def fsync(fd):
            if proceed.is_set() and threading.current_thread() is publisher[0]:
                raise OSError(errno.EIO, "disk trouble")              # only the publisher's fill
            return real_fsync(fd)
        with no_hard_links(), mock.patch.object(keystore, "_create_exclusive", create), \
                mock.patch.object(os, "fsync", fsync), \
                mock.patch.object(keystore, "WAIT_FOR_WINNER", 10.0):
            a = self.caller(out, "a")
            publisher.append(a)
            self.assertTrue(created.wait(10))
            b = self.caller(out, "b")
            b.join(0.7)
            self.assertTrue(b.is_alive(), "the second caller did not wait: %r" % (out.get("b"),))
            proceed.set()
            a.join(30)
            b.join(30)
        self.assertIsInstance(out["a"], OSError)                      # the publisher failed
        self.assertIsInstance(out["b"], bytes, "the waiting caller did not recover: %r" % (out,))
        self.assertEqual(out["b"], self.stored_key())
        self.only_the_key_file()

    def test_a_partial_key_left_by_a_crash_is_refused_after_a_bounded_wait(self):
        cases = {"empty": b"", "partial": plain(KEY)[:20], "garbage": b"x" * 50,
                 "one byte too many": plain(KEY + b"\x00"), "just the magic": keystore.MAGIC_PLAIN}
        for name, blob in cases.items():
            with self.subTest(name):
                self.write_key_file(blob)
                before = snapshot(self.folder)
                out = {}
                with mock.patch.object(keystore, "WAIT_FOR_WINNER", 0.4):
                    t0 = time.monotonic()
                    t = self.caller(out, "x")
                    t.join(10)
                    elapsed = time.monotonic() - t0
                self.assertFalse(t.is_alive(), "the caller waits for ever")
                self.assertIsInstance(out["x"], keystore.KeyFileDamaged)
                self.assertGreaterEqual(elapsed, 0.35, "it gave up without waiting")
                self.assertLess(elapsed, 5.0)
                self.assertEqual(snapshot(self.folder), before)       # byte-identical, unmade
                self.only_the_key_file()

    def test_a_damaged_key_is_never_replaced_while_waiting_either(self):
        """Even with no other caller around, the wait never ends in a new key."""
        self.write_key_file(plain(KEY[:31]))
        before = snapshot(self.folder)
        with mock.patch.object(keystore, "WAIT_FOR_WINNER", 0.3):
            with self.assertRaises(keystore.KeyFileDamaged):
                keystore.load_or_create_key(self.path)
        self.assertEqual(snapshot(self.folder), before)


class Concurrent(Tmp):
    N = 8

    def run_threads(self, ctx_factory=contextlib.nullcontext, then=None):
        barrier = threading.Barrier(self.N)
        counter, lock, real = itertools.count(1), threading.Lock(), os.urandom

        def urandom(n):
            if n == 32:
                with lock:
                    return bytes([next(counter)]) * 32                # every caller: its own key
            return real(n)
        results, errors = [], []

        def work():
            try:
                results.append(keystore.load_or_create_key(self.path))
            except BaseException as exc:                              # noqa: BLE001
                errors.append(exc)
        with ctx_factory(), mock.patch.object(os, "urandom", urandom), gated(barrier, then):
            threads = [threading.Thread(target=work) for _ in range(self.N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), self.N)
        self.assertEqual(len(set(results)), 1, "callers ended with different keys")
        self.assertEqual(results[0], self.stored_key(), "the callers' key is not the stored key")
        self.only_the_key_file()

    def test_threads_creating_the_same_key_agree(self):
        self.run_threads()

    @POSIX_ONLY
    def test_threads_agree_when_hard_links_are_unavailable(self):
        def refuse(*a, **k):
            raise OSError(errno.EPERM, "hard links are not supported here")
        self.run_threads(then=refuse)

    @POSIX_ONLY
    def test_threads_agree_on_the_emulated_windows_route(self):
        self.run_threads(ctx_factory=windows_like_rename)

    def run_processes(self, links_disabled=False):
        ctx = multiprocessing.get_context("spawn")                    # as on Windows
        barrier, out = ctx.Barrier(6), ctx.Queue()
        procs = [ctx.Process(target=_child, args=(self.path, barrier, out, links_disabled))
                 for _ in range(6)]
        for p in procs:
            p.start()
        results = [out.get(timeout=120) for _ in procs]
        for p in procs:
            p.join(60)
        self.assertEqual([r for r in results if r[0] != "ok"], [])
        keys = {r[1] for r in results}
        self.assertEqual(len(keys), 1, "processes ended with different keys")
        self.assertEqual(keys, {self.stored_key().hex()})
        self.only_the_key_file()

    def test_processes_creating_the_same_key_agree(self):
        self.run_processes()

    @POSIX_ONLY
    def test_processes_agree_when_hard_links_are_unavailable(self):
        self.run_processes(links_disabled=True)


def _child(path, barrier, out, links_disabled):
    """One of several processes creating the same key file at the same moment."""
    import errno as _errno
    import os as _os
    sys.path.insert(0, ROOT)
    from puzzle_assistant.brain import keystore as ks

    def wrap(name):
        orig, first = getattr(_os, name), [True]

        def gate(*a, **k):
            if first[0]:
                first[0] = False
                barrier.wait(60)
            if links_disabled and name == "link":
                raise OSError(_errno.EPERM, "hard links are not supported here")
            return orig(*a, **k)
        return gate
    for n in ("link", "rename", "replace"):
        setattr(_os, n, wrap(n))
    try:
        out.put(("ok", ks.load_or_create_key(path).hex()))
    except BaseException as exc:                                      # noqa: BLE001
        out.put(("err", repr(exc)))


if __name__ == "__main__":
    unittest.main()
