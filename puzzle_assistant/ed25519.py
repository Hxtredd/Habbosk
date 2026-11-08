"""Ed25519 signatures (RFC 8032), pure Python, no dependencies.

Used to check that an update was signed by the SK Assistant release key.
Verification is strict:
  * public key and R must be valid, canonically encoded curve points;
  * S must be below the group order L (no malleable signatures);
  * the check is [S]B == R + [k]A with k = SHA-512(R || A || M) mod L.

Signing is included for the owner's offline signing tool. It is not
constant-time (plain Python integers), so it must only run on the owner's
own trusted computer - never on a server or shared machine.

Checked against the RFC 8032 test vectors and, when available, against an
independent implementation (tests/test_ed25519.py).
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

Point = Tuple[int, int, int, int]          # extended coordinates (X, Y, Z, T)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _add(p: Point, q: Point) -> Point:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = 2 * t1 * t2 * D % P
    d = 2 * z1 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s: int, p: Point) -> Point:
    q: Point = (0, 1, 1, 0)                 # neutral element
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p: Point, q: Point) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % P == 0 and (y1 * z2 - y2 * z1) % P == 0


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
B: Point = (_GX, _GY, 1, _GX * _GY % P)


def _compress(p: Point) -> bytes:
    x, y, z, _ = p
    zi = pow(z, P - 2, P)
    x, y = x * zi % P, y * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s: bytes) -> Optional[Point]:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _secret_expand(seed: bytes) -> Tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("an Ed25519 private key (seed) is 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _secret_expand(seed)
    return _compress(_mul(a, B))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(seed)
    pub = _compress(_mul(a, B))
    r = int.from_bytes(_sha512(prefix + msg), "little") % L
    rs = _compress(_mul(r, B))
    k = int.from_bytes(_sha512(rs + pub + msg), "little") % L
    s = (r + k * a) % L
    return rs + int.to_bytes(s, 32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    """True only for a valid signature by `pub` over `msg`. Never raises for
    malformed input; it is simply not valid."""
    if not isinstance(pub, (bytes, bytearray)) or not isinstance(sig, (bytes, bytearray)) \
            or len(pub) != 32 or len(sig) != 64:
        return False
    a = _decompress(bytes(pub))
    if a is None:
        return False
    rs = bytes(sig[:32])
    r = _decompress(rs)
    if r is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= L:
        return False
    k = int.from_bytes(_sha512(rs + bytes(pub) + msg), "little") % L
    return _equal(_mul(s, B), _add(r, _mul(k, a)))
