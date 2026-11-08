"""Build the signed release ZIP and latest.json (read by the in-app updater).

    python tools/publish_release.py "short notes for the owner" --key PRIMARY.key [--key BACKUP.key]

Packs every file tracked by git (except dist/ and git files) into
dist/SK_Assistant_v<version>.zip (top folder 'SK Assistant'), removes older
ZIPs from dist/, and writes latest.json with size, SHA-256 and Ed25519
signatures (see UPDATE_SIGNING.md). Commit and push both; the updater then
offers the new version.

Refuses to build when:
  * puzzle_assistant/update_keys.py has no trusted key (a release without
    one could never be updated again);
  * any packed file contains a private signing key;
  * a --key is missing, stored inside a repository or not trusted by this
    release.
The private key files are read from wherever the owner keeps them (outside
any repository); nothing secret is written anywhere.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_signing  # noqa: E402


def _version(root: str) -> str:
    ns: dict = {}
    with open(os.path.join(root, "puzzle_assistant", "__init__.py"), encoding="utf-8") as fh:
        exec(compile(fh.read(), "__init__.py", "exec"), ns)
    return ns["__version__"]


def build(root: str, notes: str, key_paths) -> dict:
    update_signing.release_gate(root)
    seeds = [update_signing.load_key(p) for p in key_paths]   # fail before building anything
    if not seeds:
        raise update_signing.SigningError("give at least one --key")
    trusted, revoked = update_signing._trusted(root)
    usable = update_signing.updater._trusted_keys(trusted, revoked)
    for seed in seeds:
        kid = update_signing.updater.key_id(update_signing.ed25519.public_key(seed))
        if kid not in usable:
            raise update_signing.SigningError(f"key {kid} is not trusted by this release")
    version = _version(root)
    short = ".".join(version.split(".")[:2])
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                           check=True).stdout.split("\n")
    files = [f for f in files if f and not f.startswith(("dist/", ".git")) and f != "latest.json"]
    for f in files:
        with open(os.path.join(root, f), "rb") as fh:
            if update_signing.KEY_FILE_RE.search(fh.read()):
                raise update_signing.SigningError(f"{f} contains a private signing key - "
                                                  "remove it from the repository")
    dist = os.path.join(root, "dist")
    os.makedirs(dist, exist_ok=True)
    for old in os.listdir(dist):
        if old.endswith(".zip"):
            os.remove(os.path.join(dist, old))
    name = f"SK_Assistant_v{short}.zip"
    path = os.path.join(dist, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files):
            z.write(os.path.join(root, f), "SK Assistant/" + f)
    data = open(path, "rb").read()
    plain = {"version": version, "zip": f"dist/{name}",
             "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "notes": notes}
    # latest.json is replaced only by a signed and verified manifest: an unsigned
    # one must never be left where it could be pushed (SK 3.0 would accept it)
    work = os.path.join(dist, "latest.signing.tmp")
    with open(work, "w", encoding="utf-8") as fh:
        json.dump(plain, fh, ensure_ascii=False, indent=1)
    try:
        m = update_signing.sign_latest(work, key_paths, root)
        update_signing.verify_latest(work, root)
        os.replace(work, os.path.join(root, "latest.json"))
    finally:
        if os.path.exists(work):
            os.remove(work)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build and sign an SK Assistant release.")
    ap.add_argument("notes", nargs="?", default="")
    ap.add_argument("--key", action="append", default=[], help="private signing key file")
    a = ap.parse_args(argv)
    try:
        m = build(ROOT, a.notes, a.key)
    except (update_signing.SigningError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: m[k] for k in ("version", "zip", "sha256", "size", "notes")},
                     ensure_ascii=False))
    print("signed by:", ", ".join(s["key_id"] for s in m["signatures"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
