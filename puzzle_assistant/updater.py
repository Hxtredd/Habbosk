"""One-click updates from the owner's public GitHub repository.

How it works (owner's request: no new folders / ZIPs filling the disk):
  * `latest.json` in the repository says which version is newest, where its
    ZIP is, its size and its SHA-256 checksum;
  * SK checks it at start (if allowed in Settings) or when "Check for
    updates" is pressed - it never installs anything by itself;
  * "Update now" works in steps, and the program files are not touched until
    the whole update has been checked:
      1. download: the ZIP goes to a temporary file and its size and
         checksum are verified;
      2. stage: it is unpacked into `_update_staging\\` and checked (a real
         SK Assistant package, safe file names, intact files, the version
         the manifest promised, Python files that compile);
      3. install, as one transaction: a journal (`_update_journal.json`)
         lists every file about to change, each current file is copied to
         `_update_backup\\` before it is replaced, then the result is
         verified file by file. Any failure puts every file back; if SK is
         closed or the PC switches off half-way, the journal lets the next
         start finish the undo (recover());
      4. commit: only now is the old `_previous\\` replaced by this update's
         backup, so a working backup always exists. The download, staging
         folder and journal are removed;
  * files a newer release no longer ships are removed only if this updater
    installed them (listed in `_installed_files.json`) and they are still
    exactly as installed; they go into the backup like every other change;
  * personal data lives in %APPDATA%\\SK Assistant and is never touched,
    and neither are .venv, config files, debug or the backup folder;
  * SK then restarts itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__
from .config import app_dir

REPO = "hxtredd/habbosk"
BRANCH = "claude/windows-puzzle-assistant-1lxjat"
BASE = f"https://raw.githubusercontent.com/{REPO}/refs/heads/{BRANCH}/"
LATEST_URL = BASE + "latest.json"
UA = {"User-Agent": "SK-Assistant-Updater"}

STAGING = "_update_staging"
BACKUP = "_update_backup"
JOURNAL = "_update_journal.json"
LOCK = "_update.lock"
MANIFEST = "_installed_files.json"
JOURNAL_FORMAT = 1
# never replaced or removed by an update (top-level names, compared ignoring case)
KEEP = (".venv", "_previous", "debug", "data", "config.json", ".git",
        STAGING, BACKUP, JOURNAL, LOCK, MANIFEST)
_KEEP = {k.lower() for k in KEEP}


@dataclass
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str = ""


class UpdateError(ValueError):
    """An update that did not complete. str() is a message for the owner
    that says exactly what happened to the installed program."""

    def __init__(self, message: str, installed_changed: bool = False):
        super().__init__(message)
        self.installed_changed = installed_changed


def parse_version(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3]) or (0,)


def newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


@dataclass(frozen=True)
class Limits:
    """Upper bounds for an update. They come from the INSTALLED program, never
    from the update itself, so a package cannot raise them. The defaults are
    several times larger than a real release (3.0: 11.8 MB ZIP, 15 MB
    unpacked, 135 files, largest file 2.3 MB, worst compression 7:1) and far
    below anything that could fill a disk or exhaust memory."""
    manifest_bytes: int = 64 * 1024          # latest.json
    download_bytes: int = 64 * 1024 * 1024   # the ZIP
    extracted_bytes: int = 160 * 1024 * 1024  # all files unpacked together
    files: int = 2000                        # entries in the ZIP
    file_bytes: int = 32 * 1024 * 1024       # one unpacked file
    ratio: int = 100                         # unpacked / compressed, per file over ratio_from
    ratio_from: int = 64 * 1024
    total_ratio: int = 20                    # all unpacked bytes / ZIP size
    path_chars: int = 200                    # one file name (Windows paths stay short)


LIMITS = Limits()

SIGNED_FORMAT = 1
SIGNATURE_DOMAIN = b"SK-Assistant update manifest v1\n"   # signatures are for this use only
_VERSION_RE = re.compile(r"^\d{1,4}(\.\d{1,4}){0,2}$")
_ZIP_RE = re.compile(r"^dist/[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.zip$")


def key_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


def _trusted_keys(trusted: Optional[Dict[str, str]] = None,
                  revoked: Optional[frozenset] = None) -> Dict[str, bytes]:
    """key id -> public key bytes, without revoked keys. Entries whose id does
    not match their key are ignored."""
    if trusted is None or revoked is None:
        from . import update_keys
        trusted = update_keys.TRUSTED_KEYS if trusted is None else trusted
        revoked = update_keys.REVOKED_KEYS if revoked is None else revoked
    out = {}
    for kid, hexkey in trusted.items():
        try:
            pub = bytes.fromhex(hexkey)
        except (TypeError, ValueError):
            continue
        if len(pub) == 32 and key_id(pub) == kid and kid not in revoked:
            out[kid] = pub
    return out


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate field {k!r}")
        seen[k] = v
    return seen


def verify_manifest(raw: bytes, trusted: Optional[Dict[str, str]] = None,
                    revoked: Optional[frozenset] = None,
                    limits: Limits = LIMITS) -> dict:
    """Check latest.json and return its SIGNED content. Raises UpdateError
    when it is not signed by a trusted key or is not a valid manifest.

    latest.json keeps the plain fields older SK versions read (version, zip,
    sha256, size, notes) and adds:
      "signed":     the manifest as an exact JSON text - the only part used;
      "signatures": [{"key_id": ..., "sig": <base64 Ed25519 signature over
                     SIGNATURE_DOMAIN + the UTF-8 bytes of "signed">}, ...]
    """
    from . import ed25519
    import base64
    if len(raw) > limits.manifest_bytes:
        raise UpdateError("the update information is too large to be genuine")
    keys = _trusted_keys(trusted, revoked)
    if not keys:
        raise UpdateError("this version of SK Assistant has no trusted update key, so it "
                          "cannot accept any update (it never installs unsigned updates)")
    try:
        outer = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError(f"the update information is not valid ({exc})")
    signed = outer.get("signed") if isinstance(outer, dict) else None
    sigs = outer.get("signatures") if isinstance(outer, dict) else None
    if not isinstance(signed, str) or not isinstance(sigs, list) or not sigs:
        raise UpdateError("the update information is not signed, so it was ignored")
    message = SIGNATURE_DOMAIN + signed.encode("utf-8")
    good = False
    for entry in sigs[:8]:
        if not isinstance(entry, dict):
            continue
        pub = keys.get(entry.get("key_id"))
        if pub is None:
            continue
        try:
            sig = base64.b64decode(str(entry.get("sig", "")), validate=True)
        except ValueError:
            continue
        if ed25519.verify(pub, message, sig):
            good = True
            break
    if not good:
        raise UpdateError("the update information does not carry a valid signature from a "
                          "trusted SK Assistant key, so it was ignored")
    try:
        m = json.loads(signed, object_pairs_hook=_no_duplicates)
    except ValueError as exc:
        raise UpdateError(f"the signed update information is not valid ({exc})")
    ok = isinstance(m, dict) and m.get("format") == SIGNED_FORMAT and \
        m.get("product") == "SK Assistant" and \
        isinstance(m.get("version"), str) and _VERSION_RE.match(m["version"]) and \
        isinstance(m.get("zip"), str) and _ZIP_RE.match(m["zip"]) and \
        isinstance(m.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", m["sha256"]) and \
        type(m.get("size")) is int and 0 < m["size"] <= limits.download_bytes and \
        isinstance(m.get("notes", ""), str)
    if not ok:
        raise UpdateError("the signed update information is incomplete or out of range")
    return m


def _get(url: str, timeout: float = 15.0, limit: int = LIMITS.manifest_bytes) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)                 # never more than the limit
    if len(data) > limit:
        raise UpdateError("the update information is too large to be genuine")
    return data


def check(fetch: Callable[[str], bytes] = _get, trusted: Optional[Dict[str, str]] = None,
          revoked: Optional[frozenset] = None, limits: Limits = LIMITS) -> Optional[Release]:
    """The newest release if it is newer than this version, else None.
    Only a manifest signed by a trusted key is used. Raises UpdateError (or
    OSError when the server cannot be reached) when the check fails."""
    m = verify_manifest(fetch(LATEST_URL), trusted, revoked, limits)
    rel = Release(m["version"], BASE + m["zip"], m["sha256"], m["size"], m.get("notes", ""))
    return rel if newer(rel.version) else None


def download(rel: Release, progress: Optional[Callable[[float], None]] = None,
             opener=urllib.request.urlopen, limits: Limits = LIMITS) -> str:
    """Download to a temporary file and verify size and SHA-256 against the
    (signed) release. Never reads more than the announced size. Returns its
    path; nothing is left behind on failure."""
    if not 0 < rel.size <= limits.download_bytes:
        raise UpdateError(f"the update is larger than allowed ({rel.size} bytes)")
    fd, path = tempfile.mkstemp(prefix="sk_update_", suffix=".zip")
    h = hashlib.sha256()
    done = 0
    try:
        with os.fdopen(fd, "wb") as out, opener(urllib.request.Request(rel.url, headers=UA),
                                               timeout=30) as r:
            while True:
                chunk = r.read(min(256 * 1024, rel.size + 1 - done))
                if not chunk:
                    break
                done += len(chunk)
                if done > rel.size:
                    raise ValueError("the download is larger than announced")
                out.write(chunk)
                h.update(chunk)
                if progress and rel.size:
                    progress(min(1.0, done / rel.size))
        if done != rel.size or h.hexdigest() != rel.sha256:
            raise ValueError("the download is incomplete or damaged (checksum differs)")
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def verify_package(zip_path: str, rel: Release) -> None:
    """The file on disk is exactly the signed package (checked again right
    before installing, in case the temporary file was changed)."""
    try:
        size = os.path.getsize(zip_path)
        digest = _sha(zip_path)
    except OSError as exc:
        raise UpdateError(f"the downloaded update cannot be read ({exc})")
    if size != rel.size or digest != rel.sha256:
        raise UpdateError("the downloaded update does not match the signed release")


# ---------------------------------------------------------------------------
# small file helpers (separate functions so tests can make them fail)
# ---------------------------------------------------------------------------
def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path: str) -> None:
    if sys.platform == "win32":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def _copy_complete(src: str, dst: str) -> None:
    """Copy so that `dst` exists only once it is complete."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, dst)


def _put(src: str, dst: str) -> None:
    """Put a file in place. Prefers an atomic replace; a file Windows keeps
    open (e.g. a font in use) cannot be replaced, so it is overwritten in
    place as the old updater did - its backup already exists by then."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".sk_new"
    shutil.copy2(src, tmp)
    for attempt in range(3):
        try:
            os.replace(tmp, dst)
            _drop_bytecode(dst)
            return
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))      # e.g. a virus scanner looking at it
    try:
        shutil.copyfile(tmp, dst)
        shutil.copystat(tmp, dst)
        _drop_bytecode(dst)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _drop_bytecode(py_path: str) -> None:
    """Python reuses __pycache__ bytecode when a source file's size and
    modification time (to the second) match what it recorded. An update
    keeps the ZIP's timestamps, so a replaced .py of the same size could
    otherwise keep running its OLD code. Remove the cached bytecode of every
    .py the updater writes; Python rebuilds it from the new source."""
    if not py_path.endswith(".py"):
        return
    cache = os.path.join(os.path.dirname(py_path), "__pycache__")
    stem = os.path.basename(py_path)[:-3] + "."
    try:
        names = os.listdir(cache)
    except OSError:
        return
    for name in names:
        if name.startswith(stem) and name.endswith(".pyc"):
            try:
                os.remove(os.path.join(cache, name))
            except OSError:
                pass


def _remove(path: str) -> None:
    os.remove(path)


def _rel_ok(rel: str) -> bool:
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and ":" not in rel and \
        all(p not in ("", ".", "..") for p in parts) and parts[0].lower() not in _KEEP


def _walk(root: str) -> Dict[str, str]:
    """relative path ('/'-separated) -> absolute path, for every file."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(d, f)
            out[os.path.relpath(full, root).replace(os.sep, "/")] = full
    return out


def _os_lock(fd: int) -> None:
    """Lock byte 0 of the open lock file without waiting; OSError if taken."""
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)      # msvcrt locks from the current position
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)      # the same byte that was locked
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class _Lock:
    """Only one process installs or recovers at a time. The OS releases the
    lock if the process dies, so a crash never leaves it stuck.

    The lock file is permanent: it is never deleted, because deleting it
    would let a process that had already opened the old file and a process
    that creates a new one both 'hold' the lock. After locking, the file is
    also checked to still be the one at the path (someone could have deleted
    it by hand); if not, the lock is not taken."""

    def __init__(self, target: str):
        self.path = os.path.join(target, LOCK)
        self.fd: Optional[int] = None

    def _open(self) -> None:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        self.fd = os.open(self.path, flags, 0o644)

    def _try_lock(self) -> bool:
        try:
            _os_lock(self.fd)
        except OSError:
            self._close()
            return False
        try:
            same = os.path.samestat(os.fstat(self.fd), os.stat(self.path))
        except OSError:
            same = False
        if not same:                      # the file was replaced under us
            self.release()
            return False
        return True

    def acquire(self) -> bool:
        self._open()
        return self._try_lock()

    def _close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def release(self) -> None:
        """Unlock. The lock file stays (see above)."""
        if self.fd is None:
            return
        try:
            _os_unlock(self.fd)
        except OSError:
            pass                          # closing the file releases it anyway
        self._close()


# ---------------------------------------------------------------------------
# staging: everything is checked before the program files are touched
# ---------------------------------------------------------------------------
def _version_of(src: str) -> Optional[str]:
    try:
        with open(os.path.join(src, "puzzle_assistant", "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", fh.read(), re.M)
        return m.group(1) if m else None
    except (OSError, UnicodeDecodeError):
        return None


def _same_version(a: Optional[str], b: str) -> bool:
    """'3.1' and '3.1.0' are the same version."""
    if a is None:
        return False
    pa, pb = list(parse_version(a)), list(parse_version(b))
    n = max(len(pa), len(pb))
    return pa + [0] * (n - len(pa)) == pb + [0] * (n - len(pb))


_WIN_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | \
    {f"lpt{i}" for i in range(1, 10)}


def _unsafe_name(name: str, limits: Limits) -> Optional[str]:
    if not name or len(name) > limits.path_chars:
        return "file name too long"
    if any(ord(c) < 32 for c in name) or "\\" in name or ":" in name or name.startswith("/"):
        return "unsafe file name"
    for part in name.rstrip("/").split("/"):
        if part in ("", ".", "..") or part != part.rstrip(". ") or \
                part.split(".")[0].lower() in _WIN_RESERVED:
            return "unsafe file name"
    return None


def _inspect_zip(z: zipfile.ZipFile, zip_size: int, limits: Limits) -> List[zipfile.ZipInfo]:
    """Everything that can be checked from the ZIP's directory, before a
    single byte is unpacked. Returns the file entries to extract."""
    import stat
    import struct
    infos = z.infolist()
    if not infos:
        raise UpdateError("the update package is empty")
    if len(infos) > limits.files:
        raise UpdateError(f"the update package has too many files ({len(infos)})")
    total, seen, files = 0, set(), []
    for i in infos:
        problem = _unsafe_name(i.filename, limits)
        if problem:
            raise UpdateError(f"{problem} in the update ({i.filename[:80]!r})")
        key = i.filename.rstrip("/").lower()
        if key in seen:
            raise UpdateError(f"the update package lists a file twice ({i.filename[:80]!r})")
        seen.add(key)
        if i.flag_bits & 0x1:
            raise UpdateError("the update package contains encrypted files")
        kind = stat.S_IFMT(i.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise UpdateError(f"the update package contains a link or special file "
                              f"({i.filename[:80]!r})")
        if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise UpdateError("the update package uses an unsupported compression")
        if i.is_dir():
            if i.file_size:
                raise UpdateError("the update package is malformed")
            continue
        if i.file_size > limits.file_bytes:
            raise UpdateError(f"a file in the update is too large ({i.filename[:80]!r})")
        if i.file_size > limits.ratio_from and i.file_size > limits.ratio * max(i.compress_size, 1):
            raise UpdateError(f"a file in the update is suspiciously compressed "
                              f"({i.filename[:80]!r}) - possible ZIP bomb")
        total += i.file_size
        if total > limits.extracted_bytes:
            raise UpdateError("the update would unpack to more than allowed - possible ZIP bomb")
        files.append(i)
    if total > limits.ratio_from and total > limits.total_ratio * max(zip_size, 1):
        raise UpdateError("the update is suspiciously compressed - possible ZIP bomb")
    # entries must not share or overlap their data (another ZIP-bomb trick)
    end_of_data = getattr(z, "start_dir", None)
    spans = []
    for i in infos:
        z.fp.seek(i.header_offset)
        head = z.fp.read(30)
        if len(head) != 30 or head[:4] != b"PK\x03\x04":
            raise UpdateError("the update package is malformed")
        n, e = struct.unpack("<HH", head[26:30])
        spans.append((i.header_offset, i.header_offset + 30 + n + e + i.compress_size))
    spans.sort()
    for (s1, e1), (s2, _) in zip(spans, spans[1:]):
        if s2 < e1:
            raise UpdateError("the update package has overlapping files - possible ZIP bomb")
    if spans and end_of_data is not None and spans[-1][1] > end_of_data:
        raise UpdateError("the update package is malformed")
    return files


def _extract(z: zipfile.ZipFile, files: List[zipfile.ZipInfo], staging: str,
             limits: Limits) -> None:
    """Unpack, counting the real bytes (never trusting the declared sizes)."""
    root = os.path.realpath(staging)
    os.makedirs(root, exist_ok=True)
    total = 0
    for i in files:
        dest = os.path.join(root, *i.filename.split("/"))
        if os.path.commonpath([root, os.path.realpath(dest)]) != root:
            raise UpdateError("unsafe file name in the update")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        written = 0
        with z.open(i) as src, open(dest, "xb") as out:
            while True:
                chunk = src.read(256 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                total += len(chunk)
                if written > min(i.file_size, limits.file_bytes) or \
                        total > limits.extracted_bytes:
                    raise UpdateError("the update unpacks to more than it declares - "
                                      "possible ZIP bomb")
                out.write(chunk)
        if written != i.file_size:
            raise UpdateError(f"the update package is damaged ({i.filename[:80]!r})")


def stage(zip_path: str, target: str, expected_version: Optional[str] = None,
          limits: Limits = LIMITS) -> str:
    """Unpack and check the update in `target`/_update_staging. Returns the
    folder holding the new program files. Raises UpdateError (nothing in the
    installation has changed) when the package is not usable."""
    staging = os.path.join(target, STAGING)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        try:
            with zipfile.ZipFile(zip_path) as z:
                files = _inspect_zip(z, os.path.getsize(zip_path), limits)
                _extract(z, files, staging, limits)   # CRC of every file is checked here
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as exc:
            raise UpdateError(f"the update package is damaged or not a valid ZIP file ({exc})")
        tops = [d for d in os.listdir(staging) if os.path.isdir(os.path.join(staging, d))]
        src = os.path.join(staging, tops[0]) if len(tops) == 1 and \
            len(os.listdir(staging)) == 1 else staging
        if not os.path.exists(os.path.join(src, "puzzle_assistant", "__init__.py")):
            raise UpdateError("this is not an SK Assistant update")
        version = _version_of(src)
        if expected_version is not None and not _same_version(version, expected_version):
            raise UpdateError(f"the package contains version {version}, "
                              f"not the announced {expected_version}")
        for rel, full in _walk(src).items():     # the program's own code must compile
            if rel.endswith(".py") and (rel == "app.py" or rel.startswith("puzzle_assistant/")):
                try:
                    with open(full, "rb") as fh:
                        compile(fh.read(), rel, "exec", dont_inherit=True)
                except (SyntaxError, ValueError) as exc:
                    raise UpdateError(f"the update contains a broken file ({rel}: {exc})")
        return src
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"the update could not be unpacked ({exc})")


# ---------------------------------------------------------------------------
# the transaction
# ---------------------------------------------------------------------------
def _read_manifest(target: str) -> Dict[str, str]:
    try:
        with open(os.path.join(target, MANIFEST), encoding="utf-8") as fh:
            data = json.load(fh)
        files = data.get("files", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def _journal(target: str, state: str, entries: List[list], version: str) -> None:
    _write_atomic(os.path.join(target, JOURNAL), json.dumps(
        {"format": JOURNAL_FORMAT, "state": state, "version": version, "entries": entries},
        ensure_ascii=False).encode("utf-8"))


def _plan(src: str, target: str) -> Tuple[List[Tuple[str, str]], List[str], Dict[str, str]]:
    """(files to put: (rel, staged path)), (files to remove), new manifest."""
    new = {rel: full for rel, full in _walk(src).items() if _rel_ok(rel)}
    manifest = {rel: _sha(full) for rel, full in new.items()}
    puts = []
    for rel, full in new.items():
        old = os.path.join(target, *rel.split("/"))
        if os.path.isfile(old) and os.path.getsize(old) == os.path.getsize(full) and \
                _sha(old) == manifest[rel]:
            continue
        puts.append((rel, full))
    removes = []
    new_lower = {r.lower() for r in new}
    for rel, digest in sorted(_read_manifest(target).items()):
        if not _rel_ok(rel) or rel.lower() in new_lower:
            continue
        old = os.path.join(target, *rel.split("/"))
        # only a file this updater installed and nobody has changed since
        if os.path.isfile(old) and _sha(old) == digest:
            removes.append(rel)
    return puts, removes, manifest


_NOT_INSTALLED = "The update was not installed: {}. Nothing in the program folder was changed."
_UNREADABLE = ("An interrupted update could not be undone automatically ({}). Some program "
               "files may be from the new version; your settings and data were not touched.")
_BUSY = "An update is being installed by another SK Assistant window right now."


def install(zip_path: str, target: Optional[str] = None, expected_version: Optional[str] = None,
            packages: Optional[Callable[[List[str], str], None]] = None,
            release: Optional[Release] = None, limits: Limits = LIMITS) -> List[str]:
    """Install the verified ZIP into `target` as one transaction. Returns the
    files that changed (added, replaced or removed). The ZIP is deleted
    afterwards. On failure raises UpdateError after putting everything back.

    `packages(changed, target)` (default: update_packages) runs before the
    update is committed; if it fails the program files are restored too.
    `release` (from check(): signed) makes the ZIP be checked against its
    signed size and SHA-256 once more before anything is unpacked."""
    target = target or app_dir()
    packages = update_packages if packages is None else packages
    lock = _Lock(target)
    try:
        try:
            locked = lock.acquire()
        except OSError as exc:
            raise UpdateError(_NOT_INSTALLED.format(f"the program folder is not writable ({exc})"))
        if not locked:
            raise UpdateError(_NOT_INSTALLED.format("another update is already running"))
        try:
            note = recover(target, _locked=True)
            if os.path.exists(os.path.join(target, JOURNAL)):
                raise UpdateError(f"{note} The new update was not started.",
                                  installed_changed=True)
            if release is not None:
                expected_version = expected_version or release.version
            try:
                if release is not None:
                    verify_package(zip_path, release)
                src = stage(zip_path, target, expected_version, limits)
            except UpdateError as exc:
                raise UpdateError(_NOT_INSTALLED.format(exc)) from None
            try:
                return _transaction(src, target, expected_version or _version_of(src) or "?",
                                    packages)
            finally:
                shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
        finally:
            lock.release()
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


def _transaction(src: str, target: str, version: str,
                 packages: Callable[[List[str], str], None]) -> List[str]:
    staging = os.path.join(target, STAGING)
    man_path = os.path.join(staging, MANIFEST)
    backup = os.path.join(target, BACKUP)
    try:
        puts, removes, manifest = _plan(src, target)
        _write_atomic(man_path, json.dumps({"version": version, "files": manifest},
                                           ensure_ascii=False, indent=1).encode("utf-8"))
        shutil.rmtree(backup, ignore_errors=True)
    except OSError as exc:
        shutil.rmtree(backup, ignore_errors=True)
        raise UpdateError(_NOT_INSTALLED.format(f"it could not be prepared ({exc})"))
    entries = [[rel, os.path.isfile(os.path.join(target, *rel.split("/")))]
               for rel in [r for r, _ in puts] + removes + [MANIFEST]]
    changed = [os.path.normpath(r) for r, _ in puts] + [os.path.normpath(r) for r in removes]
    try:
        _journal(target, "installing", entries, version)
        os.makedirs(backup)
        for rel, full in puts + [(MANIFEST, man_path)]:
            dst = os.path.join(target, *rel.split("/"))
            if os.path.isfile(dst):
                _copy_complete(dst, os.path.join(backup, *rel.split("/")))
            _put(full, dst)
        for rel in removes:
            dst = os.path.join(target, *rel.split("/"))
            _copy_complete(dst, os.path.join(backup, *rel.split("/")))
            try:
                _remove(dst)
            except OSError:
                pass        # in use: left in place (harmless) - it is backed up either way
        for rel, full in puts + [(MANIFEST, man_path)]:      # verify the result
            dst = os.path.join(target, *rel.split("/"))
            if not os.path.isfile(dst) or _sha(dst) != _sha(full):
                raise UpdateError(f"{rel} did not install correctly")
        packages(changed, target)
        _journal(target, "committed", entries, version)     # the point of no return
    except BaseException as exc:
        reason = exc if isinstance(exc, (UpdateError, OSError, ValueError)) else type(exc).__name__
        undone = _rollback(target, entries)
        if not undone:
            raise UpdateError(
                f"The update to {version} failed ({reason}) and could not be fully undone. "
                "SK will finish restoring the previous version the next time it starts; "
                "your settings and data were not touched.", installed_changed=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        pkg = " Python packages in .venv may have been updated." if \
            isinstance(exc, PackageError) else ""
        raise UpdateError(f"The update to {version} could not be installed ({reason}). "
                          f"Every program file was put back as it was before the update;{pkg} "
                          "your settings and data were not touched.")
    try:
        _finish_commit(target)
    except OSError:
        pass        # installed and verified; the journal makes the next start tidy up
    return changed


def _finish_commit(target: str) -> None:
    """The new version is in place and verified: its backup becomes
    `_previous` (only now is the older backup let go)."""
    backup = os.path.join(target, BACKUP)
    previous = os.path.join(target, "_previous")
    if os.path.isdir(backup):
        shutil.rmtree(previous, ignore_errors=True)
        if os.path.exists(previous):
            raise OSError("the old _previous folder could not be removed")
        os.replace(backup, previous)
    shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
    _remove_journal_tmp(target)
    os.remove(os.path.join(target, JOURNAL))
    _fsync_dir(target)


def _rollback(target: str, entries: List[list]) -> bool:
    """Put every journal entry back as it was. Returns True if complete.
    Safe to run again after an interruption."""
    backup = os.path.join(target, BACKUP)
    ok = True
    made_dirs = set()
    for rel, existed in entries:
        if not isinstance(rel, str) or not (_rel_ok(rel) or rel == MANIFEST):
            continue
        dst = os.path.join(target, *rel.split("/"))
        saved = os.path.join(backup, *rel.split("/"))
        try:
            if os.path.isfile(saved):
                # a file that never actually changed (e.g. it was locked) needs no write
                if not (os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(saved)
                        and _sha(dst) == _sha(saved)):
                    _put(saved, dst)
            elif not existed:
                if os.path.isfile(dst):
                    _remove(dst)
                made_dirs.add(os.path.dirname(dst))
            for leftover in (dst + ".sk_new",):
                if os.path.exists(leftover):
                    os.remove(leftover)
        except OSError:
            ok = False
    for d in sorted(made_dirs, key=len, reverse=True):     # folders the update created
        while os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(target)):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)
    if not ok:
        return False
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
    _remove_journal_tmp(target)
    try:
        os.remove(os.path.join(target, JOURNAL))
    except FileNotFoundError:
        pass
    except OSError:
        return False
    _fsync_dir(target)
    return True


def _tidy_unstarted(target: str) -> None:
    """Without a journal no program file was touched (the journal is written
    before the first change and removed after the last), so what an update
    leaves behind is only its own scratch: remove it."""
    shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
    shutil.rmtree(os.path.join(target, BACKUP), ignore_errors=True)
    _remove_journal_tmp(target)


def _remove_journal_tmp(target: str) -> None:
    try:
        os.remove(os.path.join(target, JOURNAL + ".tmp"))
    except OSError:
        pass


# set by startup_check(): a note for the owner, shown in the window
LAST_RECOVERY: Optional[str] = None


def recover(target: Optional[str] = None, _locked: bool = False) -> Optional[str]:
    """Finish an update that was interrupted (SK closed, PC switched off).
    Returns None if there was nothing to do, otherwise a note."""
    target = target or app_dir()
    path = os.path.join(target, JOURNAL)
    if not any(os.path.exists(os.path.join(target, n))
               for n in (JOURNAL, STAGING, BACKUP, JOURNAL + ".tmp")):
        return None
    lock = None
    if not _locked:
        lock = _Lock(target)
        if not lock.acquire():
            return _BUSY                    # never touch an update that is running
    try:
        if not os.path.exists(path):        # stopped before installing began
            _tidy_unstarted(target)
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                j = json.load(fh)
            if j.get("format") != JOURNAL_FORMAT:
                return _UNREADABLE.format("it was written by an unknown version")
            entries = [e for e in j.get("entries", []) if isinstance(e, list) and len(e) == 2]
            state, version = j.get("state"), j.get("version", "?")
        except (OSError, ValueError, AttributeError):
            return _UNREADABLE.format("its journal cannot be read")
        if state == "committed":
            try:
                _finish_commit(target)
            except OSError:
                return f"The update to {version} is installed but could not finish tidying up."
            return f"The update to {version} was completed."
        if _rollback(target, entries):
            return (f"The update to {version} was interrupted. The previous version was "
                    "restored; your settings and data were not touched.")
        return (f"The update to {version} was interrupted and could not be fully undone yet. "
                "SK will try again at the next start.")
    finally:
        if lock is not None:
            lock.release()


def _journal_state(target: str) -> Optional[str]:
    """None without a journal, else its state ('installing', 'committed') or 'unreadable'."""
    path = os.path.join(target, JOURNAL)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            j = json.load(fh)
        if j.get("format") != JOURNAL_FORMAT:
            return "unreadable"
        return str(j.get("state"))
    except (OSError, ValueError, AttributeError):
        return "unreadable"


def startup_check(target: Optional[str] = None) -> Optional[str]:
    """Called by app.py before anything else is loaded. Finishes or undoes an
    interrupted update. Returns None when SK may start, otherwise the message
    to show instead of starting: the program files may be a mix of two
    versions. Nothing is ever deleted here when recovery cannot complete -
    the journal and the backup stay for the next attempt."""
    global LAST_RECOVERY
    target = target or app_dir()
    try:
        note = recover(target)
    except Exception as exc:                  # a fault in recovery itself
        note = f"Restoring it failed ({type(exc).__name__}: {exc})."
    LAST_RECOVERY = note
    state = _journal_state(target)
    if state is None or state == "committed":
        return None       # no update in progress, or a complete and verified one
    if note == _BUSY:
        return (f"SK Assistant cannot start yet.\n\n{_BUSY} Wait until it has finished, "
                "then start SK Assistant again.")
    return ("SK Assistant cannot start yet.\n\n"
            f"{note or 'An update was interrupted.'}\n\n"
            "SK will not run on program files that may be a mix of two versions. Nothing has "
            "been deleted: the information needed to restore the previous version is kept in "
            f"{JOURNAL} and the {BACKUP} folder in\n{target}\n"
            "Your settings and personal data are not affected.\n\n"
            "What to do: close any program that may be using files in that folder (for example "
            "an Explorer window or an editor) and start SK Assistant again - it will try again. "
            "If it keeps failing, leave the folder as it is (do not delete those files or "
            "install over them) and ask for help.")


class PackageError(OSError):
    pass


def update_packages(changed: List[str], target: Optional[str] = None) -> None:
    """New Python packages are installed only if requirements.txt changed.
    Raises PackageError if pip fails (the update is then undone)."""
    if "requirements.txt" not in [c.replace(os.sep, "/") for c in changed]:
        return
    target = target or app_dir()
    res = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                          os.path.join(target, "requirements.txt")], cwd=target,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if res.returncode != 0:
        raise PackageError(f"installing the required Python packages failed (pip exit code "
                           f"{res.returncode})")


def _same(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def restart() -> None:
    """Start the updated SK a moment after this one has closed (only one
    SK may run at a time)."""
    target = app_dir()
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-len("python.exe")] + "pythonw.exe"
        exe = w if os.path.exists(w) else exe
    code = ("import time,subprocess;time.sleep(2);"
            f"subprocess.Popen([{exe!r},{os.path.join(target, 'app.py')!r}],cwd={target!r})")
    subprocess.Popen([exe, "-c", code], cwd=target,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
