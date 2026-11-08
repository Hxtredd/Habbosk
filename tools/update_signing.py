"""Offline signing of SK Assistant updates (see UPDATE_SIGNING.md).

    python tools/update_signing.py keygen  OUTFILE
    python tools/update_signing.py pubkey  KEYFILE
    python tools/update_signing.py sign    --key KEYFILE [--key KEYFILE2] [--latest latest.json]
    python tools/update_signing.py verify  [--latest latest.json]

Run it on the owner's own computer, ideally offline. Private key files:
  * are created only OUTSIDE any git repository and outside an SK program
    folder, never overwrite an existing file, and are readable only by the
    owner where the system supports it;
  * are refused when they are found inside a git repository or an SK
    program folder (so one can never be committed or shipped by accident);
  * contain the marker below; tools/publish_release.py refuses to build a
    release if any packed file contains it.
The signing code is plain Python (not constant-time): never run it on a
shared or untrusted machine.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from puzzle_assistant import ed25519, updater  # noqa: E402

MARKER = "SK-UPDATE-PRIVATE-KEY-V1"
# what a private key file looks like (the source code mentioning MARKER does not match)
KEY_FILE_RE = re.compile(rb"SK-UPDATE-PRIVATE-KEY-V1\r?\nkey_id=[0-9a-f]{16}\r?\n"
                         rb"public=[0-9a-f]{64}\r?\nseed=[0-9a-f]{64}")


class SigningError(Exception):
    pass


# ---------------------------------------------------------------------------
# where private keys may live
# ---------------------------------------------------------------------------
def _forbidden_place(path: str) -> str:
    """Why a private key must not be at `path` ('' if the place is fine)."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return f"it is inside a git repository ({d})"
        if os.path.isfile(os.path.join(d, "app.py")) and \
                os.path.isdir(os.path.join(d, "puzzle_assistant")):
            return f"it is inside an SK Assistant program folder ({d})"
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def generate_key(path: str) -> dict:
    why = _forbidden_place(path)
    if why:
        raise SigningError(f"refusing to create a private key there: {why}")
    seed = os.urandom(32)
    pub = ed25519.public_key(seed)
    kid = updater.key_id(pub)
    text = (f"# SK Assistant update signing key - PRIVATE. Keep it offline and backed up.\n"
            f"# Never put it in a repository, the program folder, a ZIP or a chat.\n"
            f"{MARKER}\nkey_id={kid}\npublic={pub.hex()}\nseed={seed.hex()}\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # never overwrite
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(text)
    return {"key_id": kid, "public": pub.hex()}


def load_key(path: str) -> bytes:
    why = _forbidden_place(path)
    if why:
        raise SigningError(f"refusing to use a private key that is stored where it could be "
                           f"committed or shipped: {why}. Move it somewhere safe first.")
    fields = {}
    with open(path, encoding="ascii") as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not lines or lines[0] != MARKER:
        raise SigningError("this is not an SK Assistant signing key file")
    for ln in lines[1:]:
        k, _, v = ln.partition("=")
        fields[k] = v
    try:
        seed = bytes.fromhex(fields["seed"])
        pub = ed25519.public_key(seed)
    except (KeyError, ValueError):
        raise SigningError("the signing key file is damaged")
    if fields.get("public") != pub.hex() or fields.get("key_id") != updater.key_id(pub):
        raise SigningError("the signing key file is damaged (its public key does not match)")
    return seed


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------
def signed_text(version: str, zip_rel: str, sha256: str, size: int, notes: str) -> str:
    payload = {"format": updater.SIGNED_FORMAT, "product": "SK Assistant", "version": version,
               "zip": zip_rel, "sha256": sha256, "size": size, "notes": notes}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_manifest(version: str, zip_rel: str, sha256: str, size: int, notes: str,
                  seeds) -> dict:
    """latest.json: the plain fields older SK versions read, plus the signed
    manifest and one signature per key."""
    text = signed_text(version, zip_rel, sha256, size, notes)
    sigs = []
    for seed in seeds:
        sig = ed25519.sign(seed, updater.SIGNATURE_DOMAIN + text.encode("utf-8"))
        sigs.append({"key_id": updater.key_id(ed25519.public_key(seed)),
                     "sig": base64.b64encode(sig).decode("ascii")})
    return {"version": version, "zip": zip_rel, "sha256": sha256, "size": size, "notes": notes,
            "signed": text, "signatures": sigs}


def _trusted(root: str):
    """TRUSTED_KEYS / REVOKED_KEYS of the release being built (its own file)."""
    ns: dict = {}
    with open(os.path.join(root, "puzzle_assistant", "update_keys.py"), encoding="utf-8") as fh:
        exec(compile(fh.read(), "update_keys.py", "exec"), ns)
    return dict(ns["TRUSTED_KEYS"]), frozenset(ns["REVOKED_KEYS"])


def release_gate(root: str = ROOT) -> None:
    """A release must carry trusted keys, or nobody could ever update past it."""
    trusted, revoked = _trusted(root)
    if not updater._trusted_keys(trusted, revoked):
        raise SigningError("puzzle_assistant/update_keys.py has no valid trusted key. Add the "
                           "public keys first (UPDATE_SIGNING.md) - a release without them "
                           "could never be updated again.")


def sign_latest(latest_path: str, key_paths, root: str = ROOT) -> dict:
    release_gate(root)
    trusted, revoked = _trusted(root)
    with open(latest_path, encoding="utf-8") as fh:
        plain = json.load(fh)
    zip_file = os.path.join(root, *str(plain["zip"]).split("/"))
    data = open(zip_file, "rb").read()
    if len(data) != plain["size"] or hashlib.sha256(data).hexdigest() != plain["sha256"]:
        raise SigningError("latest.json does not match the ZIP it names - rebuild it first")
    seeds = [load_key(p) for p in key_paths]
    if not seeds:
        raise SigningError("give at least one --key")
    for seed in seeds:
        kid = updater.key_id(ed25519.public_key(seed))
        if kid not in updater._trusted_keys(trusted, revoked):
            raise SigningError(f"key {kid} is not trusted by this release "
                               "(puzzle_assistant/update_keys.py)")
    m = make_manifest(str(plain["version"]), str(plain["zip"]), str(plain["sha256"]),
                      int(plain["size"]), str(plain.get("notes", "")), seeds)
    raw = json.dumps(m, ensure_ascii=False, indent=1).encode("utf-8")
    updater.verify_manifest(raw, trusted, revoked)              # check before writing
    tmp = latest_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, latest_path)
    return m


def verify_latest(latest_path: str, root: str = ROOT) -> dict:
    trusted, revoked = _trusted(root)
    with open(latest_path, "rb") as fh:
        raw = fh.read()
    m = updater.verify_manifest(raw, trusted, revoked)
    data = open(os.path.join(root, *m["zip"].split("/")), "rb").read()
    if len(data) != m["size"] or hashlib.sha256(data).hexdigest() != m["sha256"]:
        raise SigningError("the ZIP does not match the signed manifest")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("keygen")
    g.add_argument("outfile")
    pk = sub.add_parser("pubkey")
    pk.add_argument("keyfile")
    s = sub.add_parser("sign")
    s.add_argument("--key", action="append", required=True)
    s.add_argument("--latest", default=os.path.join(ROOT, "latest.json"))
    v = sub.add_parser("verify")
    v.add_argument("--latest", default=os.path.join(ROOT, "latest.json"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            info = generate_key(a.outfile)
            print(f"Created {a.outfile}\nkey id:     {info['key_id']}\npublic key: {info['public']}"
                  f"\n\nAdd this line to TRUSTED_KEYS in puzzle_assistant/update_keys.py:\n"
                  f'    "{info["key_id"]}": "{info["public"]}",\n\n'
                  "Back the key file up offline (e.g. a USB stick kept elsewhere). "
                  "Never share it.")
        elif a.cmd == "pubkey":
            pub = ed25519.public_key(load_key(a.keyfile))
            print(f'"{updater.key_id(pub)}": "{pub.hex()}",')
        elif a.cmd == "sign":
            m = sign_latest(a.latest, a.key)
            print(f"Signed {m['version']} with {', '.join(x['key_id'] for x in m['signatures'])}")
        else:
            m = verify_latest(a.latest)
            print(f"OK: {m['version']} is signed by a trusted key and matches its ZIP")
    except (SigningError, updater.UpdateError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
