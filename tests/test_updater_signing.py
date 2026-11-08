"""Signed updates and package limits.

Only test keys are used (fixed seeds below). They are never trusted by a
release: test_no_test_key_is_trusted_by_the_program checks that. All files
are created in temporary folders.
"""
import base64
import hashlib
import io
import json
import os
import shutil
import stat
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
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import update_signing as US  # noqa: E402
from puzzle_assistant import ed25519, update_keys, updater  # noqa: E402
from test_updater_transactions import (NEW, OLD, USER, Base, make_zip, manifest_for,  # noqa: E402
                                       snapshot, write_tree)

SEED_A = hashlib.sha256(b"SK test key A - never trusted by a release").digest()
SEED_B = hashlib.sha256(b"SK test key B - never trusted by a release").digest()
SEED_X = hashlib.sha256(b"SK test key X - an attacker's key").digest()
TEST_SEEDS = (SEED_A, SEED_B, SEED_X)


def kid(seed):
    return updater.key_id(ed25519.public_key(seed))


def trusted(*seeds):
    return {kid(s): ed25519.public_key(s).hex() for s in seeds}


def latest(data=None, version="2.0.0", seeds=(SEED_A,), zip_rel="dist/SK_Assistant_v2.0.zip",
           notes="new things"):
    data = make_zip(NEW) if data is None else data
    return US.make_manifest(version, zip_rel, hashlib.sha256(data).hexdigest(), len(data),
                            notes, list(seeds))


def raw(m):
    return json.dumps(m).encode("utf-8")


def verify(m, keys=None, revoked=frozenset(), **kw):
    return updater.verify_manifest(raw(m) if isinstance(m, dict) else m,
                                   trusted(SEED_A) if keys is None else keys, revoked, **kw)


def resign_text(m, change):
    """The signed text altered, the old signatures kept (an attacker's edit)."""
    inner = json.loads(m["signed"])
    change(inner)
    m = dict(m)
    m["signed"] = json.dumps(inner, sort_keys=True, separators=(",", ":"))
    return m


class Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_total = 0

    def read(self, n=-1):
        chunk = super().read(n)
        self.read_total += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Manifest(unittest.TestCase):
    def test_a_correctly_signed_manifest_is_accepted(self):
        m = verify(latest())
        self.assertEqual((m["version"], m["zip"], m["notes"]),
                         ("2.0.0", "dist/SK_Assistant_v2.0.zip", "new things"))

    def test_only_the_signed_fields_are_used(self):
        m = latest(version="9.0.0")
        m.update(version="99.0.0", zip="dist/evil.zip", sha256="0" * 64, size=1, notes="evil")
        rel = updater.check(lambda url: raw(m), trusted=trusted(SEED_A), revoked=frozenset())
        self.assertEqual(rel.version, "9.0.0")
        self.assertTrue(rel.url.endswith("/dist/SK_Assistant_v2.0.zip"))
        self.assertEqual(rel.notes, "new things")

    def test_missing_signatures(self):
        m = latest()
        for change in (lambda d: d.pop("signatures"), lambda d: d.update(signatures=[]),
                       lambda d: d.pop("signed"), lambda d: d.update(signatures="x"),
                       lambda d: d.update(signed={"version": "2.0.0"})):
            bad = json.loads(json.dumps(m))
            change(bad)
            with self.assertRaises(updater.UpdateError) as cm:
                verify(bad)
            self.assertIn("not signed", str(cm.exception))
        plain = {k: m[k] for k in ("version", "zip", "sha256", "size", "notes")}
        with self.assertRaises(updater.UpdateError):   # what SK 3.0 reads: refused here
            verify(plain)

    def test_invalid_signatures(self):
        m = latest()
        sig = bytearray(base64.b64decode(m["signatures"][0]["sig"]))
        for i in (0, 33, 63):
            bad = json.loads(json.dumps(m))
            s = bytearray(sig)
            s[i] ^= 1
            bad["signatures"][0]["sig"] = base64.b64encode(bytes(s)).decode()
            with self.assertRaises(updater.UpdateError) as cm:
                verify(bad)
            self.assertIn("valid signature", str(cm.exception))
        for junk in ("", "not base64!!", base64.b64encode(b"short").decode()):
            bad = json.loads(json.dumps(m))
            bad["signatures"][0]["sig"] = junk
            with self.assertRaises(updater.UpdateError):
                verify(bad)

    def test_altered_manifest_fields(self):
        m = latest()
        for change in (lambda d: d.update(version="9.9.9"), lambda d: d.update(sha256="0" * 64),
                       lambda d: d.update(size=d["size"] + 1), lambda d: d.update(notes="evil"),
                       lambda d: d.update(zip="dist/other.zip"), lambda d: d.pop("notes")):
            with self.assertRaises(updater.UpdateError):
                verify(resign_text(m, change))
        bad = dict(m, signed=m["signed"] + " ")                   # even whitespace
        with self.assertRaises(updater.UpdateError):
            verify(bad)

    def test_signature_by_an_untrusted_or_revoked_key(self):
        with self.assertRaises(updater.UpdateError):
            verify(latest(seeds=(SEED_X,)))
        with self.assertRaises(updater.UpdateError):           # claims a trusted key's id
            m = latest(seeds=(SEED_X,))
            m["signatures"][0]["key_id"] = kid(SEED_A)
            verify(m)
        with self.assertRaises(updater.UpdateError):
            verify(latest(), keys=trusted(SEED_A, SEED_B), revoked=frozenset({kid(SEED_A)}))

    def test_a_signature_made_for_another_purpose_does_not_count(self):
        m = latest()
        sig = ed25519.sign(SEED_A, m["signed"].encode("utf-8"))      # no domain prefix
        m["signatures"] = [{"key_id": kid(SEED_A), "sig": base64.b64encode(sig).decode()}]
        with self.assertRaises(updater.UpdateError):
            verify(m)

    def test_no_trusted_key_means_no_update_at_all(self):
        for keys in ({}, {"0000000000000000": ed25519.public_key(SEED_A).hex()},
                     {kid(SEED_A): "not hex"}):
            with self.assertRaises(updater.UpdateError) as cm:
                verify(latest(), keys=keys)
            self.assertIn("no trusted update key", str(cm.exception))

    def test_duplicate_fields_are_refused(self):
        m = latest()
        inner = m["signed"]
        dup = inner[:-1] + ',"version":"9.9.9"}'
        sig = ed25519.sign(SEED_A, updater.SIGNATURE_DOMAIN + dup.encode())
        m2 = dict(m, signed=dup, signatures=[{"key_id": kid(SEED_A),
                                              "sig": base64.b64encode(sig).decode()}])
        with self.assertRaises(updater.UpdateError):
            verify(m2)
        outer = raw(m)[:-1] + b', "signed": "x"}'
        with self.assertRaises(updater.UpdateError):
            verify(outer)

    def test_signed_values_out_of_range_are_refused_even_with_a_valid_signature(self):
        data = make_zip(NEW)
        for kw in ({"zip_rel": "../SK.zip"}, {"zip_rel": "https://evil.example/x.zip"},
                   {"zip_rel": "dist/a/../../x.zip"}, {"zip_rel": "dist/x.exe"},
                   {"version": "2.0.0; rm"}, {"version": ""}):
            with self.assertRaises(updater.UpdateError):
                verify(latest(data, **kw))
        big = US.make_manifest("2.0.0", "dist/x.zip", "a" * 64,
                               updater.LIMITS.download_bytes + 1, "", [SEED_A])
        with self.assertRaises(updater.UpdateError):
            verify(big)
        for payload in ({"format": 2}, {"product": "Other"}, {"size": 0}, {"size": "10"},
                        {"sha256": "A" * 64}):
            text = json.loads(US.signed_text("2.0.0", "dist/x.zip", "a" * 64, 10, ""))
            text.update(payload)
            t = json.dumps(text, sort_keys=True, separators=(",", ":"))
            sig = ed25519.sign(SEED_A, updater.SIGNATURE_DOMAIN + t.encode())
            with self.assertRaises(updater.UpdateError):
                verify({"signed": t, "signatures": [{"key_id": kid(SEED_A),
                                                     "sig": base64.b64encode(sig).decode()}]})

    def test_oversized_manifest(self):
        m = latest(notes="x" * (updater.LIMITS.manifest_bytes + 10))
        with self.assertRaises(updater.UpdateError):
            verify(m)
        big = Resp(b"{" + b" " * (updater.LIMITS.manifest_bytes * 4) + b"}")
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=big):
            with self.assertRaises(updater.UpdateError):
                updater._get("https://example.invalid/latest.json")
        self.assertLessEqual(big.read_total, updater.LIMITS.manifest_bytes + 1)


class KeyRotation(unittest.TestCase):
    def test_rotation_by_signing_with_old_and_new_key(self):
        both = latest(seeds=(SEED_A, SEED_B))
        verify(both, keys=trusted(SEED_A))                 # installs that know only A
        verify(both, keys=trusted(SEED_B))                 # installs that know only B
        verify(latest(seeds=(SEED_B,)), keys=trusted(SEED_A, SEED_B))
        with self.assertRaises(updater.UpdateError):       # A retired
            verify(latest(seeds=(SEED_A,)), keys=trusted(SEED_A, SEED_B),
                   revoked=frozenset({kid(SEED_A)}))
        verify(both, keys=trusted(SEED_A, SEED_B), revoked=frozenset({kid(SEED_A)}))

    def test_extra_signatures_from_unknown_keys_are_ignored(self):
        verify(latest(seeds=(SEED_X, SEED_A)))

    def test_new_keys_take_effect_through_an_installed_update(self):
        """An installation trusting A installs a release whose key list
        trusts only B; after that (as after a restart) B-signed manifests are
        accepted and A-signed ones refused."""
        tmp = tempfile.mkdtemp(prefix="sk_rotation_")
        self.addCleanup(shutil.rmtree, tmp, True)
        inst = os.path.join(tmp, "inst")
        code = {}
        for f in ("__init__.py", "config.py", "updater.py", "ed25519.py"):
            with open(os.path.join(ROOT, "puzzle_assistant", f), encoding="utf-8") as fh:
                code["puzzle_assistant/" + f] = fh.read()

        def keys_py(*seeds):
            return f"TRUSTED_KEYS = {trusted(*seeds)!r}\nREVOKED_KEYS = frozenset()\n"
        old = dict(code, **{"puzzle_assistant/update_keys.py": keys_py(SEED_A)})
        old["puzzle_assistant/__init__.py"] = '__version__ = "1.0.0"\n'
        write_tree(inst, old)
        new = dict(code, **{"puzzle_assistant/update_keys.py": keys_py(SEED_B)})
        new["puzzle_assistant/__init__.py"] = '__version__ = "2.0.0"\n'
        pkg = make_zip(new)
        z = os.path.join(tmp, "u.zip")
        with open(z, "wb") as fh:
            fh.write(pkg)
        m = latest(pkg, seeds=(SEED_A,))
        mf = os.path.join(tmp, "latest.json")
        with open(mf, "w") as fh:
            json.dump(m, fh)
        run = textwrap.dedent("""
            import json, sys
            sys.path.insert(0, sys.argv[1])
            from puzzle_assistant import updater, __version__
            cmd = sys.argv[2]
            if cmd == "install":
                rel = updater.check(lambda url: open(sys.argv[3], "rb").read())
                updater.install(sys.argv[4], sys.argv[1], packages=lambda c, t: None, release=rel)
                print("installed")
            else:
                try:
                    rel = updater.check(lambda url: open(sys.argv[3], "rb").read())
                    print("accepted", rel.version)
                except updater.UpdateError as e:
                    print("refused")
        """)
        script = os.path.join(tmp, "run.py")
        with open(script, "w") as fh:
            fh.write(run)

        def go(*args):
            r = subprocess.run([sys.executable, script, inst] + list(args), capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()
        self.assertEqual(go("install", mf, z), "installed")       # signed by A: accepted
        for seed, expect in ((SEED_B, "accepted 3.0.0"), (SEED_A, "refused")):
            with open(mf, "w") as fh:
                json.dump(latest(version="3.0.0", seeds=(seed,)), fh)
            self.assertEqual(go("check", mf), expect)


class Download(unittest.TestCase):
    def rel(self, data, size=None):
        return updater.Release("2.0.0", "https://example.invalid/u.zip",
                               hashlib.sha256(data).hexdigest(), len(data) if size is None else size)

    def test_oversized_release_is_refused_before_downloading(self):
        opener = mock.Mock()
        with self.assertRaises(updater.UpdateError):
            updater.download(self.rel(b"x", size=updater.LIMITS.download_bytes + 1),
                             opener=opener)
        opener.assert_not_called()

    def test_server_sending_more_than_announced_is_cut_off(self):
        data = make_zip(NEW)
        served = Resp(data + b"\0" * (5 * 1024 * 1024))
        with self.assertRaises(ValueError):
            updater.download(self.rel(data), opener=lambda req, timeout: served)
        self.assertLessEqual(served.read_total, len(data) + 1)

    def test_altered_package_is_refused(self):
        data = make_zip(NEW)
        evil = make_zip(dict(NEW, **{"puzzle_assistant/a.py": "EVIL = 1\n"}))
        with self.assertRaises(ValueError):
            updater.download(self.rel(data), opener=lambda req, timeout: Resp(evil))


class InstallChecksThePackage(Base):
    def test_package_swapped_after_download_is_refused(self):
        data = make_zip(NEW)
        rel = updater.Release("2.0.0", "u", hashlib.sha256(data).hexdigest(), len(data))
        before = snapshot(self.target)
        evil = make_zip(dict(NEW, **{"puzzle_assistant/a.py": "EVIL = 1\n"}))
        with self.assertRaises(updater.UpdateError) as cm:
            self.install(evil, release=rel)
        self.assertIn("does not match the signed release", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)
        self.install(data, release=rel)
        self.assertEqual(snapshot(self.target)["puzzle_assistant/a.py"], b"A = 2\n")

    def test_signed_version_must_match_the_package(self):
        data = make_zip(dict(NEW, **{"puzzle_assistant/__init__.py": '__version__ = "1.5.0"\n'}))
        rel = updater.Release("2.0.0", "u", hashlib.sha256(data).hexdigest(), len(data))
        with self.assertRaises(updater.UpdateError):
            self.install(data, release=rel)

    def test_end_to_end_signed_update(self):
        data = make_zip(NEW)
        m = latest(data)
        with mock.patch.object(updater, "newer", return_value=True):
            rel = updater.check(lambda url: raw(m), trusted=trusted(SEED_A), revoked=frozenset())
        path = updater.download(rel, opener=lambda req, timeout: Resp(data))
        updater.install(path, self.target, packages=lambda c, t: None, release=rel)
        self.assertEqual(snapshot(self.target)["puzzle_assistant/a.py"], b"A = 2\n")
        self.assertFalse(os.path.exists(path))


def bomb_zip(files, compression=zipfile.ZIP_DEFLATED, top="SK Assistant"):
    return make_zip(files, top=top, compression=compression)


class PackageLimits(Base):
    """Unsafe packages are refused before any program file changes."""

    def refused(self, data, limits=updater.LIMITS, contains=None):
        before = snapshot(self.target)
        with self.assertRaises(updater.UpdateError) as cm:
            self.install(data, limits=limits)
        self.assertEqual(snapshot(self.target), before)
        self.assert_clean()
        self.assertIn("Nothing in the program folder was changed", str(cm.exception))
        if contains:
            self.assertIn(contains, str(cm.exception))
        return str(cm.exception)

    def test_the_real_release_fits_the_default_limits(self):
        real = os.path.join(ROOT, "dist", "SK_Assistant_v3.0.zip")
        if not os.path.exists(real):
            self.skipTest("no release ZIP in this checkout")
        with zipfile.ZipFile(real) as z:
            files = updater._inspect_zip(z, os.path.getsize(real), updater.LIMITS)
        self.assertGreater(len(files), 100)

    def test_zip_bomb_by_compression_ratio(self):
        self.refused(bomb_zip(dict(NEW, **{"assets/zeros.bin": b"\0" * (3 * 1024 * 1024)})),
                     contains="ZIP bomb")

    def test_per_file_ratio_on_its_own(self):
        # a bomb hidden among incompressible files passes the overall ratio
        lim = updater.Limits(total_ratio=10 ** 6)
        self.refused(bomb_zip(dict(NEW, **{"assets/zeros.bin": b"\0" * (512 * 1024),
                                           "assets/noise.bin": os.urandom(256 * 1024)})),
                     limits=lim, contains="suspiciously compressed (")

    def test_zip_bomb_by_total_size(self):
        lim = updater.Limits(extracted_bytes=200 * 1024, ratio=10 ** 6, total_ratio=10 ** 6)
        files = dict(NEW, **{f"assets/f{i}.bin": os.urandom(1024) * 50 for i in range(5)})
        self.refused(bomb_zip(files), limits=lim, contains="more than allowed")

    def test_overall_compression_ratio(self):
        lim = updater.Limits(total_ratio=5, ratio=10 ** 6)       # per-file check out of the way
        files = dict(NEW, **{f"assets/t{i}.txt": "SK " * 30000 for i in range(4)})
        self.refused(bomb_zip(files), limits=lim, contains="ZIP bomb")

    def test_too_many_files(self):
        lim = updater.Limits(files=10)
        self.refused(bomb_zip(dict(NEW, **{f"x/{i}.txt": "x" for i in range(10)})), limits=lim,
                     contains="too many files")

    def test_one_file_too_large(self):
        lim = updater.Limits(file_bytes=100 * 1024)
        self.refused(bomb_zip(dict(NEW, **{"assets/big.bin": os.urandom(150 * 1024)})),
                     limits=lim, contains="too large")

    def test_overlapping_entries_bomb(self):
        data = bytearray(bomb_zip({"a.txt": "same", "b.txt": "same"}, top=""))
        cds = [i for i in range(len(data)) if data[i:i + 4] == b"PK\x01\x02"]
        first_offset = data[cds[0] + 42:cds[0] + 46]
        data[cds[1] + 42:cds[1] + 46] = first_offset        # b points at a's data
        self.refused(bytes(data), contains="overlapping")

    def test_declared_size_smaller_than_real_content(self):
        data = bytearray(bomb_zip(dict(NEW), compression=zipfile.ZIP_STORED))
        z = zipfile.ZipFile(io.BytesIO(bytes(data)))
        info = z.getinfo("SK Assistant/puzzle_assistant/a.py")
        cd = data.find(b"PK\x01\x02", 0)
        while data[cd + 46:cd + 46 + len(info.filename)] != info.filename.encode():
            cd = data.find(b"PK\x01\x02", cd + 4)
        data[cd + 24:cd + 28] = (1).to_bytes(4, "little")          # uncompressed size: 1
        self.refused(bytes(data))

    def test_path_traversal_and_windows_names(self):
        for name in ("../escape.py", "a/../../escape.py", "/etc/x", "C:/x.py", "C:x.py",
                     "a\\..\\..\\x.py", "dir/CON", "aux.txt", "dir/lpt1.log", "trailing./x.py",
                     "space /x.py", "nul\x00.py", "tab\t.py", "x" * 300):
            files = dict(NEW)
            files[name] = "x"
            self.refused(bomb_zip(files, top=""))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.py")))

    def test_symlink_entry(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in NEW.items():
                z.writestr("SK Assistant/" + name, data)
            info = zipfile.ZipInfo("SK Assistant/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
        self.refused(buf.getvalue(), contains="link")

    def test_encrypted_entry(self):
        data = bytearray(bomb_zip(dict(NEW)))
        cd = data.find(b"PK\x01\x02")
        data[cd + 8] |= 1                                        # "encrypted" flag
        self.refused(bytes(data))

    def test_unpacking_counts_real_bytes_not_declared_sizes(self):
        """zipfile already stops at the declared size; this checks the
        updater's own count in case a ZIP reader ever does not."""
        class LyingZip:
            def open(self, info):
                return io.BytesIO(b"x" * (1024 * 1024))           # far more than declared
        info = zipfile.ZipInfo("SK Assistant/a.bin")
        info.file_size = 10
        staging = os.path.join(self.tmp, "staging")
        with self.assertRaises(updater.UpdateError) as cm:
            updater._extract(LyingZip(), [info], staging, updater.LIMITS)
        self.assertIn("more than it declares", str(cm.exception))
        self.assertLessEqual(os.path.getsize(os.path.join(staging, "SK Assistant", "a.bin")),
                             256 * 1024)
        info.file_size = 2 * 1024 * 1024                          # declares more than it has
        shutil.rmtree(staging)
        with self.assertRaises(updater.UpdateError):
            updater._extract(LyingZip(), [info], staging, updater.LIMITS)

    def test_unsupported_compression(self):
        self.refused(bomb_zip(dict(NEW), compression=zipfile.ZIP_BZIP2), contains="compression")


INTERRUPT = textwrap.dedent("""
    import os, shutil, sys
    sys.path.insert(0, sys.argv[1])
    from puzzle_assistant import updater
    target, zip_path, n, sha, size = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], int(sys.argv[6])
    count = [0]

    def wrap(mod, name):
        real = getattr(mod, name)
        def f(*a, **kw):
            count[0] += 1
            if count[0] == n:
                os._exit(9)
            return real(*a, **kw)
        setattr(mod, name, f)
    for mod, name in ((os, "replace"), (os, "remove"), (os, "makedirs"), (os, "fsync"),
                      (shutil, "copy2"), (shutil, "copyfile"), (shutil, "rmtree")):
        wrap(mod, name)
    rel = updater.Release("2.0.0", "u", sha, size)
    updater.install(zip_path, target, packages=lambda c, t: None, release=rel)
    print("FINISHED")
""")


class InterruptedSignedUpdate(Base):
    def test_killed_at_every_step_of_a_verified_signed_update(self):
        data = make_zip(NEW)
        sha, size = hashlib.sha256(data).hexdigest(), str(len(data))
        script = os.path.join(self.tmp, "interrupt.py")
        with open(script, "w") as fh:
            fh.write(INTERRUPT)
        target = os.path.join(self.tmp, "inst")

        def fresh():
            shutil.rmtree(target, ignore_errors=True)
            os.makedirs(target)
            write_tree(target, OLD)
            write_tree(target, USER)
            write_tree(target, {updater.MANIFEST: json.dumps(manifest_for(OLD))})
        fresh()
        old = snapshot(target)
        n, outcomes = 1, {"old": 0, "new": 0}
        while True:
            fresh()
            z = os.path.join(self.tmp, "s.zip")
            with open(z, "wb") as fh:
                fh.write(data)
            r = subprocess.run([sys.executable, script, ROOT, target, z, str(n), sha, size],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                break
            self.assertEqual(r.returncode, 9, r.stderr)
            self.assertIsNone(updater.startup_check(target))
            snap = snapshot(target)
            if snap == old:
                outcomes["old"] += 1
            else:
                self.assertEqual(snap["puzzle_assistant/a.py"], b"A = 2\n", f"step {n}")
                self.assertEqual(snap["app.py"], NEW["app.py"].encode())
                outcomes["new"] += 1
            n += 1
        self.assertGreater(outcomes["old"], 10)
        self.assertGreater(outcomes["new"], 0)
        updater.LAST_RECOVERY = None


class SigningTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sk_signing_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.keys = os.path.join(self.tmp, "offline-keys")
        os.makedirs(self.keys)

    def key_file(self, seed, name="k.key"):
        path = os.path.join(self.keys, name)
        pub = ed25519.public_key(seed)
        with open(path, "w") as fh:
            fh.write(f"{US.MARKER}\nkey_id={updater.key_id(pub)}\npublic={pub.hex()}\n"
                     f"seed={seed.hex()}\n")
        return path

    def repo(self, keys=()):
        root = os.path.join(self.tmp, "repo")
        files = {"app.py": "print(1)\n", "puzzle_assistant/__init__.py": '__version__ = "9.1.0"\n',
                 "puzzle_assistant/update_keys.py":
                     f"TRUSTED_KEYS = {trusted(*keys)!r}\nREVOKED_KEYS = frozenset()\n",
                 "README.txt": "hello\n"}
        write_tree(root, files)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        return root

    def publish(self, root, key_paths):
        import publish_release
        return publish_release.build(root, "notes", key_paths)

    def test_keygen_creates_a_usable_private_key_only_outside_repositories(self):
        path = os.path.join(self.keys, "primary.key")
        info = US.generate_key(path)
        seed = US.load_key(path)
        self.assertEqual(ed25519.public_key(seed).hex(), info["public"])
        self.assertEqual(updater.key_id(bytes.fromhex(info["public"])), info["key_id"])
        self.assertTrue(US.KEY_FILE_RE.search(open(path, "rb").read()))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with self.assertRaises(FileExistsError):                 # never overwritten
            US.generate_key(path)
        root = self.repo()
        for where in (os.path.join(root, "k.key"), os.path.join(root, "tools", "k.key")):
            with self.assertRaises(US.SigningError):
                US.generate_key(where)
        prog = os.path.join(self.tmp, "SK Assistant")
        write_tree(prog, {"app.py": "", "puzzle_assistant/__init__.py": ""})
        with self.assertRaises(US.SigningError):
            US.generate_key(os.path.join(prog, "k.key"))

    def test_a_key_stored_inside_a_repository_is_refused(self):
        root = self.repo()
        inside = os.path.join(root, "k.key")
        shutil.copy(self.key_file(SEED_A), inside)
        with self.assertRaises(US.SigningError):
            US.load_key(inside)

    def test_damaged_key_file_is_refused(self):
        path = self.key_file(SEED_A)
        text = open(path).read().replace("public=", "public=00")
        with open(path, "w") as fh:
            fh.write(text)
        with self.assertRaises(US.SigningError):
            US.load_key(path)

    def test_publish_refuses_without_trusted_keys(self):
        root = self.repo(keys=())
        with self.assertRaises(US.SigningError) as cm:
            self.publish(root, [self.key_file(SEED_A)])
        self.assertIn("no valid trusted key", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(root, "latest.json")))
        self.assertFalse(os.path.exists(os.path.join(root, "dist")))

    def test_publish_refuses_an_untrusted_or_missing_key(self):
        root = self.repo(keys=(SEED_A,))
        for keys in ([self.key_file(SEED_X)], []):
            with self.assertRaises(US.SigningError):
                self.publish(root, keys)
        self.assertFalse(os.path.exists(os.path.join(root, "latest.json")))
        self.assertFalse(os.path.exists(os.path.join(root, "dist")))

    def test_publish_refuses_a_private_key_among_the_packed_files(self):
        root = self.repo(keys=(SEED_A,))
        shutil.copy(self.key_file(SEED_B, "b.key"), os.path.join(root, "notes.txt"))
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        with self.assertRaises(US.SigningError) as cm:
            self.publish(root, [self.key_file(SEED_A)])
        self.assertIn("private signing key", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(root, "latest.json")))

    def test_publish_builds_a_signed_release_that_the_updater_accepts(self):
        root = self.repo(keys=(SEED_A, SEED_B))
        m = self.publish(root, [self.key_file(SEED_A, "a.key"), self.key_file(SEED_B, "b.key")])
        with open(os.path.join(root, "latest.json"), "rb") as fh:
            got = updater.verify_manifest(fh.read(), trusted(SEED_B), frozenset())
        self.assertEqual(got["version"], "9.1.0")
        self.assertEqual(sorted(s["key_id"] for s in m["signatures"]),
                         sorted([kid(SEED_A), kid(SEED_B)]))
        with open(os.path.join(root, "latest.json")) as fh:      # SK 3.0 still reads these
            plain = json.load(fh)
        self.assertEqual((plain["version"], plain["zip"]), ("9.1.0", "dist/SK_Assistant_v9.1.zip"))
        zip_bytes = open(os.path.join(root, "dist", "SK_Assistant_v9.1.zip"), "rb").read()
        self.assertEqual(plain["sha256"], hashlib.sha256(zip_bytes).hexdigest())
        self.assertNotIn(b"seed=", zip_bytes)
        self.assertFalse(os.path.exists(os.path.join(root, "dist", "latest.signing.tmp")))
        US.verify_latest(os.path.join(root, "latest.json"), root)

    def test_cli_round_trip(self):
        path = os.path.join(self.keys, "cli.key")
        tool = os.path.join(ROOT, "tools", "update_signing.py")
        r = subprocess.run([sys.executable, tool, "keygen", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TRUSTED_KEYS", r.stdout)
        self.assertNotIn(open(path).read().split("seed=")[1].strip(), r.stdout)  # never printed
        r = subprocess.run([sys.executable, tool, "pubkey", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class StaleBytecode(Base):
    def test_a_replaced_module_never_runs_its_old_bytecode(self):
        """Worst case: the new a.py has the same size and the same modification
        time as the old one, and Python has cached the old one's bytecode."""
        imp = [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
               "from puzzle_assistant import a; print(a.A)", self.target]
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        r = subprocess.run(imp, capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), "1", r.stderr)          # bytecode cached now
        a_py = os.path.join(self.target, "puzzle_assistant", "a.py")
        old_mtime = os.stat(a_py).st_mtime
        self.install()                                               # a.py: "A = 2", same size
        os.utime(a_py, (old_mtime, old_mtime))
        r = subprocess.run(imp, capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), "2", r.stderr)


class NoSecretsShipped(unittest.TestCase):
    def test_no_private_key_in_the_repository(self):
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                               check=True).stdout.split("\n")
        found = []
        for f in filter(None, files):
            p = os.path.join(ROOT, f)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    if US.KEY_FILE_RE.search(fh.read()):
                        found.append(f)
        self.assertEqual(found, [])

    def test_no_test_key_is_trusted_by_the_program(self):
        test_ids = {kid(s) for s in TEST_SEEDS}
        self.assertEqual(test_ids & set(update_keys.TRUSTED_KEYS), set())
        for k, v in update_keys.TRUSTED_KEYS.items():            # well-formed entries only
            self.assertEqual(updater.key_id(bytes.fromhex(v)), k)


if __name__ == "__main__":
    unittest.main()
