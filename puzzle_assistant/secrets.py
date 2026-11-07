"""API keys, stored encrypted (never in config.json, never on GitHub).

Windows: DPAPI (CryptProtectData) ties the encryption to the Windows user
account, so data/keys.bin is useless on another account or PC.
Other systems (development only): the file is kept private (mode 600) and
marked as not encrypted.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from typing import Dict, Optional

from .config import DATA_DIR

KEYS_PATH = os.path.join(DATA_DIR, "keys.bin")


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    # flag 0x1 = CRYPTPROTECT_UI_FORBIDDEN (never show a dialog)
    if encrypt:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "Habbosk", None, None, None,
                                      0x1, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                        0x1, ctypes.byref(blob_out))
    if not ok:
        raise OSError("Windows could not protect/unprotect the key file")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _load(path: str = KEYS_PATH) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.startswith(b"DPAPI:"):
            raw = _dpapi(raw[6:], encrypt=False)
        elif raw.startswith(b"PLAIN:"):
            raw = base64.b64decode(raw[6:])
        else:
            return {}
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _store(keys: Dict[str, str], path: str = KEYS_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    raw = json.dumps(keys).encode("utf-8")
    if sys.platform == "win32":
        data = b"DPAPI:" + _dpapi(raw, encrypt=True)
    else:
        data = b"PLAIN:" + base64.b64encode(raw)
    with open(path, "wb") as fh:
        fh.write(data)
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def get_key(name: str, path: str = KEYS_PATH) -> Optional[str]:
    return _load(path).get(name) or None


def set_key(name: str, value: Optional[str], path: str = KEYS_PATH) -> None:
    keys = _load(path)
    if value:
        keys[name] = value.strip()
    else:
        keys.pop(name, None)
    _store(keys, path)


def has_key(name: str, path: str = KEYS_PATH) -> bool:
    return bool(get_key(name, path))


def encrypted() -> bool:
    return sys.platform == "win32"
