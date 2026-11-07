"""One-click updates from the owner's public GitHub repository.

How it works (owner's request: no new folders / ZIPs filling the disk):
  * `latest.json` in the repository says which version is newest, where its
    ZIP is, its size and its SHA-256 checksum;
  * SK checks it at start (if allowed in Settings) or when "Check for
    updates" is pressed - it never installs anything by itself;
  * "Update now": the ZIP is downloaded to a temporary file, verified
    (size + checksum), unpacked, and the program files are replaced IN THE
    SAME FOLDER. Only files that differ are written; the replaced ones are
    kept in `_previous\\` (one copy, overwritten next time) so a broken
    update can be undone. The download is deleted afterwards;
  * personal data lives in %APPDATA%\\SK Assistant and is never touched,
    and neither are .venv, config files, debug or the backup folder;
  * SK then restarts itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from . import __version__
from .config import app_dir

REPO = "hxtredd/habbosk"
BRANCH = "claude/windows-puzzle-assistant-1lxjat"
BASE = f"https://raw.githubusercontent.com/{REPO}/refs/heads/{BRANCH}/"
LATEST_URL = BASE + "latest.json"
UA = {"User-Agent": "SK-Assistant-Updater"}
# never replaced or removed by an update
KEEP = (".venv", "_previous", "debug", "data", "config.json", ".git")


@dataclass
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str = ""


def parse_version(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3]) or (0,)


def newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def _get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check(fetch: Callable[[str], bytes] = _get) -> Optional[Release]:
    """The newest release if it is newer than this version, else None.
    Raises OSError / ValueError when the check itself fails."""
    data = json.loads(fetch(LATEST_URL).decode("utf-8"))
    rel = Release(str(data["version"]), BASE + str(data["zip"]), str(data["sha256"]).lower(),
                  int(data["size"]), str(data.get("notes", "")))
    return rel if newer(rel.version) else None


def download(rel: Release, progress: Optional[Callable[[float], None]] = None,
             opener=urllib.request.urlopen) -> str:
    """Download to a temporary file and verify it. Returns its path."""
    fd, path = tempfile.mkstemp(prefix="sk_update_", suffix=".zip")
    h = hashlib.sha256()
    done = 0
    try:
        with os.fdopen(fd, "wb") as out, opener(urllib.request.Request(rel.url, headers=UA),
                                               timeout=30) as r:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and rel.size:
                    progress(min(1.0, done / rel.size))
        if done != rel.size or h.hexdigest() != rel.sha256:
            raise ValueError("the download is incomplete or damaged (checksum differs)")
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def install(zip_path: str, target: Optional[str] = None) -> List[str]:
    """Replace the program files in `target` with the ZIP's. Returns the
    files that changed. The ZIP is deleted afterwards."""
    target = target or app_dir()
    work = tempfile.mkdtemp(prefix="sk_update_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():                # no paths outside the folder
                if n.startswith("/") or ".." in n.replace("\\", "/").split("/"):
                    raise ValueError("unsafe file name in the update")
            z.extractall(work)
        tops = [d for d in os.listdir(work) if os.path.isdir(os.path.join(work, d))]
        src = os.path.join(work, tops[0]) if len(tops) == 1 else work
        if not os.path.exists(os.path.join(src, "puzzle_assistant", "__init__.py")):
            raise ValueError("this is not an SK Assistant update")
        backup = os.path.join(target, "_previous")
        shutil.rmtree(backup, ignore_errors=True)
        changed = []
        for root, dirs, files in os.walk(src):
            rel_root = os.path.relpath(root, src)
            dirs[:] = [d for d in dirs if not (rel_root == "." and d in KEEP)]
            for f in files:
                rel = os.path.normpath(os.path.join(rel_root, f))
                if rel in KEEP:
                    continue
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


def _same(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def update_packages(changed: List[str], target: Optional[str] = None) -> None:
    """New Python packages are installed only if requirements.txt changed."""
    if "requirements.txt" not in changed:
        return
    target = target or app_dir()
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                    os.path.join(target, "requirements.txt")], cwd=target,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def restart() -> None:
    """Start the updated SK a moment after this one has closed (only one
    SK may run at a time)."""
    target = app_dir()
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        w = exe[:-len("python.exe")] + "pythonw.exe"
        exe = w if os.path.exists(w) else exe
    code = ("import time,subprocess;time.sleep(2);"
            f"subprocess.Popen([{exe!r},{os.path.join(target, 'app.py')!r}],cwd={target!r})")
    subprocess.Popen([exe, "-c", code], cwd=target,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
