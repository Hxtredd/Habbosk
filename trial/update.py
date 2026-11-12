"""Update the SK Assistant TRIAL in place (TRIAL_UPDATE.bat).

Owner's request (2026-11-12): "make it so I can just update the trial".

  1. Reads trial_dist/trial_latest.json on the development branch (which
     trial build is newest: its commit, ZIP, size and SHA-256).
  2. Same commit as this folder (trial/BUILD.json)? Nothing to do.
  3. Otherwise downloads the ZIP to a temporary file and checks size and
     SHA-256 - a damaged or different file is never installed.
  4. Replaces the program files IN THIS FOLDER only. Never touched: .venv,
     trial_data (the trial's settings, memory, logs), trial_results, debug.
     Replaced files are kept in _previous\\ (one copy).
  5. New packages only if requirements.txt changed; then the trial's quick
     self-test runs.

Everything runs with the isolation guard on: the normal SK Assistant's data
folder can't even be read. The normal SK program folder is never touched (it
isn't this folder). Development builds only - releases still come only
through SK's normal updater after the owner's approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from typing import Callable, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import guard  # noqa: E402

REPO = "hxtredd/habbosk"
BRANCH = "claude/kind-dijkstra-usz63w"
BASE = f"https://raw.githubusercontent.com/{REPO}/refs/heads/{BRANCH}/"
MANIFEST = BASE + "trial_dist/trial_latest.json"
UA = {"User-Agent": "SK-Assistant-Trial-Updater"}
# never replaced: the trial's own environment, data, results and the backup
KEEP = (".venv", "trial_data", "trial_results", "_previous", "debug", ".git")


class UpdateError(RuntimeError):
    pass


def _get(url: str, timeout: float = 20.0) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def current_commit(root: str = guard.TRIAL_ROOT) -> str:
    try:
        with open(os.path.join(root, "trial", "BUILD.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("commit", ""))
    except Exception:
        return ""


def latest(fetch: Callable[[str], bytes] = _get) -> dict:
    data = json.loads(fetch(MANIFEST).decode("utf-8"))
    for k in ("commit", "zip", "size", "sha256"):
        if k not in data:
            raise UpdateError(f"the update information is incomplete ({k} missing)")
    if not str(data["zip"]).startswith("trial_dist/SK_Assistant_TRIAL_"):
        raise UpdateError("the update information points somewhere unexpected")
    return data


def download(info: dict, fetch: Callable[[str], bytes] = _get) -> str:
    blob = fetch(BASE + info["zip"])
    if len(blob) != int(info["size"]) or hashlib.sha256(blob).hexdigest() != str(info["sha256"]).lower():
        raise UpdateError("the download is incomplete or damaged (checksum differs) - nothing was changed")
    fd, path = tempfile.mkstemp(prefix="sk_trial_update_", suffix=".zip")
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    return path


def _same(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def install(zip_path: str, target: str = guard.TRIAL_ROOT) -> List[str]:
    """Replace this trial folder's program files with the ZIP's. Returns the
    files that changed."""
    if not os.path.isfile(os.path.join(target, "trial", "guard.py")):
        raise UpdateError("this is not an SK Assistant TRIAL folder - nothing was changed")
    work = tempfile.mkdtemp(prefix="sk_trial_update_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():
                if n.startswith("/") or ".." in n.replace("\\", "/").split("/"):
                    raise UpdateError("unsafe file name in the update - nothing was changed")
            z.extractall(work)
        src = os.path.join(work, "SK Assistant TRIAL")
        if not (os.path.isfile(os.path.join(src, "trial", "guard.py"))
                and os.path.isfile(os.path.join(src, "puzzle_assistant", "__init__.py"))):
            raise UpdateError("the download is not an SK Assistant TRIAL - nothing was changed")
        backup = os.path.join(target, "_previous")
        shutil.rmtree(backup, ignore_errors=True)
        changed = []
        for root, dirs, files in os.walk(src):
            rel_root = os.path.relpath(root, src)
            dirs[:] = [d for d in dirs if not (rel_root == "." and d in KEEP)]
            for f in files:
                rel = os.path.normpath(os.path.join(rel_root, f))
                new, old = os.path.join(root, f), os.path.join(target, rel)
                if os.path.exists(old) and _same(new, old):
                    continue
                if os.path.exists(old):
                    os.makedirs(os.path.dirname(os.path.join(backup, rel)), exist_ok=True)
                    shutil.copy2(old, os.path.join(backup, rel))
                os.makedirs(os.path.dirname(old), exist_ok=True)
                shutil.copy2(new, old)
                changed.append(rel)
        return changed
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.remove(zip_path)
        except OSError:
            pass


def sk_running() -> bool:
    """The normal SK or the trial is open (they share one Windows lock)."""
    if sys.platform != "win32":
        return False
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "PuzzleAnswerAssistant.Single")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def run(fetch: Callable[[str], bytes] = _get, target: str = guard.TRIAL_ROOT,
        say: Callable[[str], None] = print, after: Optional[Callable[[List[str]], int]] = None) -> int:
    """0 = updated or already up to date, 1 = failed / nothing changed."""
    have = current_commit(target)
    try:
        info = latest(fetch)
    except UpdateError as exc:
        say(f"[ERROR] {exc}")
        return 1
    except Exception as exc:
        say(f"[ERROR] Couldn't check for a new trial ({exc.__class__.__name__}). "
            "Check the internet connection and try again.")
        return 1
    if info["commit"] == have:
        say(f"The trial is up to date (build {have[:7]}).")
        return 0
    say(f"New trial build {str(info['commit'])[:7]} (this folder: {have[:7] or 'unknown'}).")
    if info.get("changes"):
        say(f"  What's new: {info['changes']}")
    say("Downloading and checking it…")
    try:
        path = download(info, fetch)
        changed = install(path, target)
    except UpdateError as exc:
        say(f"[ERROR] {exc}")
        return 1
    except Exception as exc:
        say(f"[ERROR] Update failed ({exc.__class__.__name__}: {exc}). "
            "Replaced files, if any, are in _previous\\.")
        return 1
    say(f"Updated: {len(changed)} files replaced (old copies in _previous\\).")
    if after is not None:
        return after(changed)
    return 0


def _after(changed: List[str]) -> int:
    py = sys.executable
    if "requirements.txt" in changed:
        print("New packages needed - installing (requirements.txt)…")
        r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                            os.path.join(guard.TRIAL_ROOT, "requirements.txt")], cwd=guard.TRIAL_ROOT)
        if r.returncode != 0:
            print("[ERROR] Package installation failed. Check the internet connection and run "
                  "TRIAL_UPDATE.bat again.")
            return 1
    print("Running the trial's quick self-test (about a minute)…")
    r = subprocess.run([py, os.path.join(guard.TRIAL_ROOT, "trial", "selftest.py")], cwd=guard.TRIAL_ROOT)
    return r.returncode


def main() -> int:
    if sk_running():
        print("Close SK Assistant first (the trial or the normal one), then run TRIAL_UPDATE.bat again.")
        return 1
    protected = guard.normal_data_dirs()
    guard.check_layout(protected)
    g = guard.Guard(protected, allow_read=[])            # the update never needs the normal data
    g.install()
    rc = run(after=_after)
    if g.blocked:
        print(f"[WARNING] {g.blocked} blocked attempt(s) to reach the normal SK data.")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
