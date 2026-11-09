"""Keys for a B5.2 library, kept OUTSIDE the library's knowledge files.

    host_key = load_or_create_key(path_of("host.key"))
    library_key = load_or_create_key(path_of("library.key"))

On Windows the key file holds the key encrypted with DPAPI for the current
Windows user (CryptProtectData through ctypes - nothing to install). Another
Windows user cannot decrypt it. A program running as the SAME Windows user
can (that is the documented limit: real protection against such programs
needs separate Windows accounts, files encrypted with a secret only the
person knows, or a server).

Elsewhere (development, tests, the Linux candidate lab) the key file is plain
bytes with owner-only permissions; the lab reads its key from a hosting
secret instead (never a file in the repository).

Creating a key is safe against two programs starting at the same moment: each one
writes its key to its OWN unique temporary file (created exclusively, owner-only)
and then publishes it WITHOUT overwriting (a hard link on Linux / macOS, a rename
that refuses an existing target on Windows). Whoever publishes first wins; every
other caller discards its own key and loads the published one. A key file that is
damaged, empty, truncated or of the wrong length is refused with a clear error and
left exactly as it is - a new key is never made over it.
"""

from __future__ import annotations

import errno
import os
import secrets
import sys
import time

MAGIC_DPAPI = b"SKK1D"
MAGIC_PLAIN = b"SKK1P"
KEY_BYTES = 32
MAX_FILE_BYTES = 4096        # a key file is ~40 bytes (plain) or a few hundred (DPAPI)
WAIT_FOR_WINNER = 2.0        # seconds a losing caller waits for a half-written key file

# Windows: rename refuses an existing target, so it is the no-overwrite publication
# there; elsewhere a hard link is (os.rename / os.replace would overwrite silently).
_PUBLISH_BY_RENAME = sys.platform == "win32"
_NO_HARD_LINKS = {errno.EPERM, errno.ENOSYS, errno.EXDEV, errno.EMLINK}
try:
    _NO_HARD_LINKS |= {errno.ENOTSUP, errno.EOPNOTSUPP}    # same number on Linux, not on macOS
except AttributeError:                                      # pragma: no cover
    pass


class KeyFileDamaged(OSError):
    """The key file is not a usable SK key file (wrong format, truncated, wrong length)."""


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))  # UI forbidden
    if not ok:
        raise OSError("Windows could not protect / unprotect the key (DPAPI)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(key: bytes) -> bytes:
    if sys.platform == "win32":
        return MAGIC_DPAPI + _dpapi(key, True)
    return MAGIC_PLAIN + key


def unprotect(blob: bytes) -> bytes:
    """The key inside a key file's bytes. Exactly KEY_BYTES (32) bytes or an error:
    a damaged, empty, truncated or oversized key is never accepted."""
    if blob.startswith(MAGIC_DPAPI):
        if sys.platform != "win32":
            raise OSError("this key is protected by Windows for another computer / user")
        key = _dpapi(blob[len(MAGIC_DPAPI):], False)
    elif blob.startswith(MAGIC_PLAIN):
        key = blob[len(MAGIC_PLAIN):]
    else:
        raise KeyFileDamaged("not an SK key file")
    if len(key) != KEY_BYTES:
        raise KeyFileDamaged("damaged SK key file: it holds %d key bytes, not %d"
                             % (len(key), KEY_BYTES))
    return key


def _read_key_file(path: str) -> bytes:
    """Read and check an existing key file. Reads at most MAX_FILE_BYTES; never writes."""
    with open(path, "rb") as fh:
        blob = fh.read(MAX_FILE_BYTES + 1)
    if len(blob) > MAX_FILE_BYTES:
        raise KeyFileDamaged("damaged SK key file: larger than %d bytes" % MAX_FILE_BYTES)
    return unprotect(blob)


def _read_published(path: str) -> bytes:
    """The key another caller published. Where hard links are unavailable that caller
    creates the final file and then fills it, so a half-written file may be seen for a
    moment: wait for it (a damaged file that stays damaged is refused, unchanged)."""
    end = time.monotonic() + WAIT_FOR_WINNER
    while True:
        try:
            return _read_key_file(path)
        except KeyFileDamaged:
            if time.monotonic() >= end:
                raise
            time.sleep(0.02)


def _create_exclusive(path: str) -> int:
    """A NEW file, owner-only, binary; fails if anything (even a symbolic link) is there."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)                   # whatever the umask allowed (not on every system)
    except AttributeError:                     # pragma: no cover
        pass
    return fd


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_fd(fd: int, key: bytes) -> None:
    # O_BINARY is defence in depth: os.fdopen(fd, "wb") below already switches the
    # descriptor to binary mode (CPython's FileIO calls _setmode(fd, O_BINARY); the
    # Windows check confirmed it), so the key bytes are never translated either way.
    # The flag keeps the descriptor binary from the moment it is opened.
    with os.fdopen(fd, "wb") as fh:
        fh.write(protect(key))
        fh.flush()
        os.fsync(fh.fileno())


def _write_temp(folder: str, base: str, key: bytes) -> str:
    """The protected key in a unique, exclusively created, owner-only temporary file.
    Names are random and never reused, so a stale or planted file (or a symbolic link)
    at any name is never written through."""
    for _ in range(100):
        tmp = os.path.join(folder, ".%s.%s.tmp" % (base, secrets.token_hex(8)))
        try:
            fd = _create_exclusive(tmp)
        except FileExistsError:
            continue
        break
    else:
        raise OSError("could not create a temporary key file in " + folder)
    try:
        _write_fd(fd, key)
    except BaseException:
        _remove(tmp)
        raise
    return tmp


def _publish_exclusive(tmp: str, path: str) -> bool:
    """No hard links here (some network / FAT file systems): create the final file
    exclusively and fill it. Never an overwrite - O_EXCL fails if anything is there."""
    with open(tmp, "rb") as src:
        data = src.read()
    try:
        fd = _create_exclusive(path)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        _remove(path)                          # ours (we created it): never leave a partial key
        raise
    return True


def _publish(tmp: str, path: str) -> bool:
    """Make `tmp` the key file at `path` ONLY IF nothing is there. True: published.
    False: another caller's file is already there - nothing was changed. This never
    overwrites: os.replace (and, outside Windows, os.rename) would, so they are not used."""
    if _PUBLISH_BY_RENAME:
        for _ in range(20):
            try:
                os.rename(tmp, path)           # Windows: refuses an existing target
                return True
            except FileExistsError:
                return False
            except PermissionError:            # also how Windows says "target in use"
                if os.path.lexists(path):
                    return False
                time.sleep(0.05)               # a virus scanner holding the new file: retry
        os.rename(tmp, path)
        return True
    try:
        os.link(tmp, path)                     # fails if `path` exists (even as a symbolic link)
        return True
    except FileExistsError:
        return False
    except OSError as exc:
        if exc.errno not in _NO_HARD_LINKS:
            raise
    return _publish_exclusive(tmp, path)


def load_or_create_key(path: str) -> bytes:
    """The key stored at `path`, created (32 random bytes) if missing. Never
    printed or logged; only its protected form is written. An existing key file that
    is damaged or of the wrong length raises and is left untouched. If another caller
    publishes a key first, this caller's key is discarded and the published key returned."""
    if os.path.exists(path):
        return _read_key_file(path)
    key = os.urandom(KEY_BYTES)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = _write_temp(folder, os.path.basename(path), key)
    try:
        won = _publish(tmp, path)
    finally:
        _remove(tmp)                           # published (the data lives on at `path`) or lost
    if won:
        return key
    return _read_published(path)
