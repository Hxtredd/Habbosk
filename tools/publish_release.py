"""Build the release ZIP and latest.json (read by the in-app updater).

    python tools/publish_release.py "short notes for the owner"

Packs every file tracked by git (except dist/ and git files) into
dist/SK_Assistant_v<version>.zip (top folder 'SK Assistant'), removes older
ZIPs from dist/, and writes latest.json with size and SHA-256. Commit and
push both; the updater then offers the new version.
"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from puzzle_assistant import __version__  # noqa: E402


# Development-only files that must never ship (v3.4): benchmarks and their
# owner results, the isolated trial (launcher, guard, packages), the tests
# that need them, and the test-only TLS key (its tests skip without it). tests/test_release_package.py checks this list.
EXCLUDE_PREFIX = ("dist/", ".git", "benchmarks/", "trial/", "trial_dist/", "tests/fixtures/tls/")
EXCLUDE_EXACT = {"latest.json", "tools/build_trial.py", "tools/deliberate_breaks_v34.py",
                 "tests/test_trial.py", "tests/trial_e2e_harness.py",
                 "tests/test_ai_speed_check.py"}


def release_files(files):
    """The tracked files that go into the release ZIP."""
    return [f for f in files if f and not f.startswith(EXCLUDE_PREFIX) and f not in EXCLUDE_EXACT]


def main() -> None:
    notes = sys.argv[1] if len(sys.argv) > 1 else ""
    short = ".".join(__version__.split(".")[:2])
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split("\n")
    files = release_files(files)
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    for old in os.listdir(dist):
        if old.endswith(".zip"):
            os.remove(os.path.join(dist, old))
    name = f"SK_Assistant_v{short}.zip"
    path = os.path.join(dist, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files):
            z.write(os.path.join(ROOT, f), "SK Assistant/" + f)
    data = open(path, "rb").read()
    latest = {"version": __version__, "zip": f"dist/{name}",
              "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "notes": notes}
    with open(os.path.join(ROOT, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(latest, fh, ensure_ascii=False, indent=1)
    print(json.dumps(latest, ensure_ascii=False))


if __name__ == "__main__":
    main()
