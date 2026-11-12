"""Build the isolated SK Assistant TRIAL package from one exact commit.

    python tools/build_trial.py [<commit>] ["what's new"]      (default: HEAD)

Writes trial_dist/SK_Assistant_TRIAL_<short commit>.zip with the top folder
"SK Assistant TRIAL". Development use only: this is NOT a release (no
latest.json, no version change, SK's updater never sees it). Also writes
trial_dist/trial_latest.json, which the trial's own TRIAL_UPDATE.bat reads
(commit, ZIP, size, SHA-256) to update a trial folder in place.

Left out on purpose, so the trial can't touch the normal installation:
INSTALL.bat (creates the Desktop / Start menu icons), CREATE_SHORTCUTS.bat
and tools/create_shortcuts.ps1, RUN*.bat, the Brain tools (BRAIN_*.bat), the
release files (dist/, latest.json, tools/publish_release.py) and the
benchmark result records. The trial's own .bat files and README go to the
top folder; trial/BUILD.json records the commit.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP = "SK Assistant TRIAL"
EXCLUDE_EXACT = {"latest.json", "INSTALL.bat", "CREATE_SHORTCUTS.bat", "RUN.bat", "RUN_DEBUG.bat",
                 "TEST_SCREENSHOT.bat", "tools/create_shortcuts.ps1", "tools/publish_release.py",
                 "tools/build_trial.py", ".gitignore", ".gitattributes"}
EXCLUDE_PREFIX = ("dist/", "trial_dist/", ".git", "BRAIN_", "benchmarks/ai_speed_check/baseline/",
                  "benchmarks/ai_speed_check/after_A-D/")
TO_TOP = {"trial/TRIAL_SETUP.bat", "trial/TRIAL_START.bat", "trial/TRIAL_SUMMARY.bat",
          "trial/TRIAL_FULL_SELFTEST.bat", "trial/TRIAL_README.txt", "trial/TRIAL_UPDATE.bat"}


def git(*args, binary=False):
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8")


def included(path: str) -> bool:
    return path not in EXCLUDE_EXACT and not path.startswith(EXCLUDE_PREFIX)


def crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


CHANGES = "Work helper (walking tasks, :beber, safeguards) and money count numbered by the game, on top of v3.4.1"


def build(rev: str = "HEAD", changes: str = CHANGES) -> str:
    commit = git("rev-parse", rev).strip()
    files = [f for f in git("ls-tree", "-r", "--name-only", commit).split("\n") if f]
    keep = [f for f in files if included(f)]
    os.makedirs(os.path.join(ROOT, "trial_dist"), exist_ok=True)
    out = os.path.join(ROOT, "trial_dist", f"SK_Assistant_TRIAL_{commit[:7]}.zip")
    info = {"commit": commit, "built": _dt.datetime.now().isoformat(timespec="seconds"),
            "kind": "trial (development build, not a release)",
            "changes": changes}
    stamp = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(keep):
            data = git("show", f"{commit}:{f}", binary=True)
            name = os.path.basename(f) if f in TO_TOP else f
            if name.endswith((".bat", ".txt")) and (f in TO_TOP or name.endswith(".bat")):
                data = crlf(data)
            zi = zipfile.ZipInfo(f"{TOP}/{name}", stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
        zi = zipfile.ZipInfo(f"{TOP}/trial/BUILD.json", stamp)
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, json.dumps(info, indent=1))
    sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
    rel = os.path.relpath(out, ROOT).replace(os.sep, "/")
    manifest = {"commit": commit, "zip": rel, "size": os.path.getsize(out), "sha256": sha,
                "built": info["built"], "changes": changes}
    with open(os.path.join(ROOT, "trial_dist", "trial_latest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")
    print(json.dumps({"zip": rel, "commit": commit, "files": len(keep) + 1,
                      "size": os.path.getsize(out), "sha256": sha}, indent=1))
    return out


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "HEAD", sys.argv[2] if len(sys.argv) > 2 else CHANGES)
