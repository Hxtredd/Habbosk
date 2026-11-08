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
"""
from __future__ import annotations

import os
import sys

MAGIC_DPAPI = b"SKK1D"
MAGIC_PLAIN = b"SKK1P"


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
    if blob.startswith(MAGIC_DPAPI):
        if sys.platform != "win32":
            raise OSError("this key is protected by Windows for another computer / user")
        return _dpapi(blob[len(MAGIC_DPAPI):], False)
    if blob.startswith(MAGIC_PLAIN):
        return blob[len(MAGIC_PLAIN):]
    raise OSError("not an SK key file")


def load_or_create_key(path: str) -> bytes:
    """The key stored at `path`, created (32 random bytes) if missing. Never
    printed or logged; only its protected form is written."""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return unprotect(fh.read())
    key = os.urandom(32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(protect(key))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return key
