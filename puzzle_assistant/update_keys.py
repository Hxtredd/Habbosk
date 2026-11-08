"""Public keys trusted to sign SK Assistant updates (see UPDATE_SIGNING.md).

Only PUBLIC keys belong here. Private keys never go into this repository,
the installed program or a release ZIP: they stay offline with the owner.

  TRUSTED_KEYS   key id -> 32-byte Ed25519 public key (hex). An update is
                 accepted only if it carries a valid signature by at least
                 one of these keys. Ship two: a primary key and a backup
                 key kept separately offline (for rotation and recovery).
  REVOKED_KEYS   key ids that are never accepted again, even if they are
                 still listed above (e.g. after a key was lost or exposed).

The key id is the first 16 hex characters of SHA-256(public key); the
signing tool prints both when it creates a key.

THIS LIST IS EMPTY ON PURPOSE: no production key has been created yet.
With an empty list the updater refuses every update (it never falls back
to unsigned updates), and tools/publish_release.py refuses to build a
release. The owner adds the public keys before the first signed release.
"""
from typing import Dict, FrozenSet

TRUSTED_KEYS: Dict[str, str] = {
    # "0123456789abcdef": "<64 hex characters of the public key>",   # primary
    # "fedcba9876543210": "<64 hex characters of the public key>",   # backup
}

REVOKED_KEYS: FrozenSet[str] = frozenset()
