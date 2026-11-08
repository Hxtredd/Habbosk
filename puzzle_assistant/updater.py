"""One-click updates from the owner's public GitHub repository.

How it works (owner's request: no new folders / ZIPs filling the disk):
  * `latest.json` in the repository says which version is newest, where its
    ZIP is, its size and its SHA-256 checksum;
  * SK checks it at start (if allowed in Settings) or when "Check for
    updates" is pressed - it never installs anything by itself;
  * "Update now" works in steps, and the program files are not touched until
    the whole update has been checked:
      1. download: the ZIP goes to a temporary file and its size and
         checksum are verified;
      2. stage: it is unpacked into `_update_staging\\` and checked (a real
         SK Assistant package, safe file names, intact files, the version
         the manifest promised, Python files that compile);
      3. install, as one transaction: a journal (`_update_journal.json`)
         lists every file about to change, each current file is copied to
         `_update_backup\\` before it is replaced, then the result is
         verified file by file. Any failure puts every file back; if SK is
         closed or the PC switches off half-way, the journal lets the next
         start finish the undo (recover());
      4. commit: only now is the old `_previous\\` replaced by this update's
         backup, so a working backup always exists. The download, staging
         folder and journal are removed;
  * files a newer release no longer ships are removed only if this updater
    installed them (listed in `_installed_files.json`) and they are still
    exactly as installed; they go into the backup like every other change;
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
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__
from .config import app_dir

REPO = "hxtredd/habbosk"
BRANCH = "claude/windows-puzzle-assistant-1lxjat"
BASE = f"https://raw.githubusercontent.com/{REPO}/refs/heads/{BRANCH}/"
LATEST_URL = BASE + "latest.json"
UA = {"User-Agent": "SK-Assistant-Updater"}

STAGING = "_update_staging"
BACKUP = "_update_backup"
JOURNAL = "_update_journal.json"
LOCK = "_update.lock"
MANIFEST = "_installed_files.json"
JOURNAL_FORMAT = 1
# never replaced or removed by an update (top-level names, compared ignoring case)
KEEP = (".venv", "_previous", "debug", "data", "config.json", ".git",
        STAGING, BACKUP, JOURNAL, LOCK, MANIFEST)
_KEEP = {k.lower() for k in KEEP}


@dataclass
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str = ""


class UpdateError(ValueError):
    """An update that did not complete. str() is a message for the owner
    that says exactly what happened to the installed program."""

    def __init__(self, message: str, installed_changed: bool = False):
        super().__init__(message)
        self.installed_changed = installed_changed


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


# ---------------------------------------------------------------------------
# small file helpers (separate functions so tests can make them fail)
# ---------------------------------------------------------------------------
def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(path: str) -> None:
    if sys.platform == "win32":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


def _copy_complete(src: str, dst: str) -> None:
    """Copy so that `dst` exists only once it is complete."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, dst)


def _put(src: str, dst: str) -> None:
    """Put a file in place. Prefers an atomic replace; a file Windows keeps
    open (e.g. a font in use) cannot be replaced, so it is overwritten in
    place as the old updater did - its backup already exists by then."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".sk_new"
    shutil.copy2(src, tmp)
    for attempt in range(3):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))      # e.g. a virus scanner looking at it
    try:
        shutil.copyfile(tmp, dst)
        shutil.copystat(tmp, dst)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _remove(path: str) -> None:
    os.remove(path)


def _rel_ok(rel: str) -> bool:
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and ":" not in rel and \
        all(p not in ("", ".", "..") for p in parts) and parts[0].lower() not in _KEEP


def _walk(root: str) -> Dict[str, str]:
    """relative path ('/'-separated) -> absolute path, for every file."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(d, f)
            out[os.path.relpath(full, root).replace(os.sep, "/")] = full
    return out


class _Lock:
    """Only one process installs or recovers at a time. The OS releases the
    lock if the process dies, so a crash never leaves it stuck."""

    def __init__(self, target: str):
        self.path = os.path.join(target, LOCK)
        self.fh = None

    def acquire(self) -> bool:
        self.fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self) -> None:
        """Unlock and remove the lock file (left alone if someone else has it)."""
        if self.fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.fh.close()
        self.fh = None
        try:
            os.remove(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# staging: everything is checked before the program files are touched
# ---------------------------------------------------------------------------
def _version_of(src: str) -> Optional[str]:
    try:
        with open(os.path.join(src, "puzzle_assistant", "__init__.py"), encoding="utf-8") as fh:
            m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", fh.read(), re.M)
        return m.group(1) if m else None
    except (OSError, UnicodeDecodeError):
        return None


def _same_version(a: Optional[str], b: str) -> bool:
    """'3.1' and '3.1.0' are the same version."""
    if a is None:
        return False
    pa, pb = list(parse_version(a)), list(parse_version(b))
    n = max(len(pa), len(pb))
    return pa + [0] * (n - len(pa)) == pb + [0] * (n - len(pb))


def stage(zip_path: str, target: str, expected_version: Optional[str] = None) -> str:
    """Unpack and check the update in `target`/_update_staging. Returns the
    folder holding the new program files. Raises UpdateError (nothing in the
    installation has changed) when the package is not usable."""
    staging = os.path.join(target, STAGING)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        try:
            with zipfile.ZipFile(zip_path) as z:
                names = z.namelist()
                if not names:
                    raise UpdateError("the update package is empty")
                for n in names:                   # no paths outside the folder
                    norm = n.replace("\\", "/")
                    if norm.startswith("/") or ":" in norm or ".." in norm.split("/"):
                        raise UpdateError("unsafe file name in the update")
                bad = z.testzip()                 # every file's CRC
                if bad is not None:
                    raise UpdateError(f"the update package is damaged ({bad})")
                z.extractall(staging)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as exc:
            raise UpdateError(f"the update package is not a valid ZIP file ({exc})")
        tops = [d for d in os.listdir(staging) if os.path.isdir(os.path.join(staging, d))]
        src = os.path.join(staging, tops[0]) if len(tops) == 1 and \
            len(os.listdir(staging)) == 1 else staging
        if not os.path.exists(os.path.join(src, "puzzle_assistant", "__init__.py")):
            raise UpdateError("this is not an SK Assistant update")
        version = _version_of(src)
        if expected_version is not None and not _same_version(version, expected_version):
            raise UpdateError(f"the package contains version {version}, "
                              f"not the announced {expected_version}")
        for rel, full in _walk(src).items():     # the program's own code must compile
            if rel.endswith(".py") and (rel == "app.py" or rel.startswith("puzzle_assistant/")):
                try:
                    with open(full, "rb") as fh:
                        compile(fh.read(), rel, "exec", dont_inherit=True)
                except (SyntaxError, ValueError) as exc:
                    raise UpdateError(f"the update contains a broken file ({rel}: {exc})")
        return src
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"the update could not be unpacked ({exc})")


# ---------------------------------------------------------------------------
# the transaction
# ---------------------------------------------------------------------------
def _read_manifest(target: str) -> Dict[str, str]:
    try:
        with open(os.path.join(target, MANIFEST), encoding="utf-8") as fh:
            data = json.load(fh)
        files = data.get("files", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def _journal(target: str, state: str, entries: List[list], version: str) -> None:
    _write_atomic(os.path.join(target, JOURNAL), json.dumps(
        {"format": JOURNAL_FORMAT, "state": state, "version": version, "entries": entries},
        ensure_ascii=False).encode("utf-8"))


def _plan(src: str, target: str) -> Tuple[List[Tuple[str, str]], List[str], Dict[str, str]]:
    """(files to put: (rel, staged path)), (files to remove), new manifest."""
    new = {rel: full for rel, full in _walk(src).items() if _rel_ok(rel)}
    manifest = {rel: _sha(full) for rel, full in new.items()}
    puts = []
    for rel, full in new.items():
        old = os.path.join(target, *rel.split("/"))
        if os.path.isfile(old) and os.path.getsize(old) == os.path.getsize(full) and \
                _sha(old) == manifest[rel]:
            continue
        puts.append((rel, full))
    removes = []
    new_lower = {r.lower() for r in new}
    for rel, digest in sorted(_read_manifest(target).items()):
        if not _rel_ok(rel) or rel.lower() in new_lower:
            continue
        old = os.path.join(target, *rel.split("/"))
        # only a file this updater installed and nobody has changed since
        if os.path.isfile(old) and _sha(old) == digest:
            removes.append(rel)
    return puts, removes, manifest


_NOT_INSTALLED = "The update was not installed: {}. Nothing in the program folder was changed."
_UNREADABLE = ("An interrupted update could not be undone automatically ({}). Some program "
               "files may be from the new version; your settings and data were not touched. "
               "Please reinstall SK Assistant over this folder.")


def install(zip_path: str, target: Optional[str] = None, expected_version: Optional[str] = None,
            packages: Optional[Callable[[List[str], str], None]] = None) -> List[str]:
    """Install the verified ZIP into `target` as one transaction. Returns the
    files that changed (added, replaced or removed). The ZIP is deleted
    afterwards. On failure raises UpdateError after putting everything back.

    `packages(changed, target)` (default: update_packages) runs before the
    update is committed; if it fails the program files are restored too."""
    target = target or app_dir()
    packages = update_packages if packages is None else packages
    lock = _Lock(target)
    try:
        try:
            locked = lock.acquire()
        except OSError as exc:
            raise UpdateError(_NOT_INSTALLED.format(f"the program folder is not writable ({exc})"))
        if not locked:
            raise UpdateError(_NOT_INSTALLED.format("another update is already running"))
        try:
            note = recover(target, _locked=True)
            if os.path.exists(os.path.join(target, JOURNAL)):
                raise UpdateError(f"{note} The new update was not started.",
                                  installed_changed=True)
            try:
                src = stage(zip_path, target, expected_version)
            except UpdateError as exc:
                raise UpdateError(_NOT_INSTALLED.format(exc)) from None
            try:
                return _transaction(src, target, expected_version or _version_of(src) or "?",
                                    packages)
            finally:
                shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
        finally:
            lock.release()
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


def _transaction(src: str, target: str, version: str,
                 packages: Callable[[List[str], str], None]) -> List[str]:
    staging = os.path.join(target, STAGING)
    man_path = os.path.join(staging, MANIFEST)
    backup = os.path.join(target, BACKUP)
    try:
        puts, removes, manifest = _plan(src, target)
        _write_atomic(man_path, json.dumps({"version": version, "files": manifest},
                                           ensure_ascii=False, indent=1).encode("utf-8"))
        shutil.rmtree(backup, ignore_errors=True)
    except OSError as exc:
        shutil.rmtree(backup, ignore_errors=True)
        raise UpdateError(_NOT_INSTALLED.format(f"it could not be prepared ({exc})"))
    entries = [[rel, os.path.isfile(os.path.join(target, *rel.split("/")))]
               for rel in [r for r, _ in puts] + removes + [MANIFEST]]
    changed = [os.path.normpath(r) for r, _ in puts] + [os.path.normpath(r) for r in removes]
    try:
        _journal(target, "installing", entries, version)
        os.makedirs(backup)
        for rel, full in puts + [(MANIFEST, man_path)]:
            dst = os.path.join(target, *rel.split("/"))
            if os.path.isfile(dst):
                _copy_complete(dst, os.path.join(backup, *rel.split("/")))
            _put(full, dst)
        for rel in removes:
            dst = os.path.join(target, *rel.split("/"))
            _copy_complete(dst, os.path.join(backup, *rel.split("/")))
            try:
                _remove(dst)
            except OSError:
                pass        # in use: left in place (harmless) - it is backed up either way
        for rel, full in puts + [(MANIFEST, man_path)]:      # verify the result
            dst = os.path.join(target, *rel.split("/"))
            if not os.path.isfile(dst) or _sha(dst) != _sha(full):
                raise UpdateError(f"{rel} did not install correctly")
        packages(changed, target)
        _journal(target, "committed", entries, version)     # the point of no return
    except BaseException as exc:
        reason = exc if isinstance(exc, (UpdateError, OSError, ValueError)) else type(exc).__name__
        undone = _rollback(target, entries)
        if not undone:
            raise UpdateError(
                f"The update to {version} failed ({reason}) and could not be fully undone. "
                "SK will finish restoring the previous version the next time it starts; "
                "your settings and data were not touched.", installed_changed=True)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        pkg = " Python packages in .venv may have been updated." if \
            isinstance(exc, PackageError) else ""
        raise UpdateError(f"The update to {version} could not be installed ({reason}). "
                          f"Every program file was put back as it was before the update;{pkg} "
                          "your settings and data were not touched.")
    try:
        _finish_commit(target)
    except OSError:
        pass        # installed and verified; the journal makes the next start tidy up
    return changed


def _finish_commit(target: str) -> None:
    """The new version is in place and verified: its backup becomes
    `_previous` (only now is the older backup let go)."""
    backup = os.path.join(target, BACKUP)
    previous = os.path.join(target, "_previous")
    if os.path.isdir(backup):
        shutil.rmtree(previous, ignore_errors=True)
        if os.path.exists(previous):
            raise OSError("the old _previous folder could not be removed")
        os.replace(backup, previous)
    shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
    _remove_journal_tmp(target)
    os.remove(os.path.join(target, JOURNAL))
    _fsync_dir(target)


def _rollback(target: str, entries: List[list]) -> bool:
    """Put every journal entry back as it was. Returns True if complete.
    Safe to run again after an interruption."""
    backup = os.path.join(target, BACKUP)
    ok = True
    made_dirs = set()
    for rel, existed in entries:
        if not isinstance(rel, str) or not (_rel_ok(rel) or rel == MANIFEST):
            continue
        dst = os.path.join(target, *rel.split("/"))
        saved = os.path.join(backup, *rel.split("/"))
        try:
            if os.path.isfile(saved):
                _put(saved, dst)
            elif not existed:
                if os.path.isfile(dst):
                    _remove(dst)
                made_dirs.add(os.path.dirname(dst))
            for leftover in (dst + ".sk_new",):
                if os.path.exists(leftover):
                    os.remove(leftover)
        except OSError:
            ok = False
    for d in sorted(made_dirs, key=len, reverse=True):     # folders the update created
        while os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(target)):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)
    if not ok:
        return False
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
    _remove_journal_tmp(target)
    try:
        os.remove(os.path.join(target, JOURNAL))
    except FileNotFoundError:
        pass
    except OSError:
        return False
    _fsync_dir(target)
    return True


def _tidy_unstarted(target: str) -> None:
    """Without a journal no program file was touched (the journal is written
    before the first change and removed after the last), so what an update
    leaves behind is only its own scratch: remove it."""
    shutil.rmtree(os.path.join(target, STAGING), ignore_errors=True)
    shutil.rmtree(os.path.join(target, BACKUP), ignore_errors=True)
    _remove_journal_tmp(target)


def _remove_journal_tmp(target: str) -> None:
    try:
        os.remove(os.path.join(target, JOURNAL + ".tmp"))
    except OSError:
        pass


# set by recover_at_start(): a note for the owner, shown in the window
LAST_RECOVERY: Optional[str] = None


def recover(target: Optional[str] = None, _locked: bool = False) -> Optional[str]:
    """Finish an update that was interrupted (SK closed, PC switched off).
    Returns None if there was nothing to do, otherwise a note."""
    target = target or app_dir()
    path = os.path.join(target, JOURNAL)
    if not any(os.path.exists(os.path.join(target, n))
               for n in (JOURNAL, STAGING, BACKUP, JOURNAL + ".tmp")):
        return None
    lock = None
    if not _locked:
        lock = _Lock(target)
        if not lock.acquire():
            return None                     # an update is running right now
    try:
        if not os.path.exists(path):        # stopped before installing began
            _tidy_unstarted(target)
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                j = json.load(fh)
            if j.get("format") != JOURNAL_FORMAT:
                return _UNREADABLE.format("it was written by an unknown version")
            entries = [e for e in j.get("entries", []) if isinstance(e, list) and len(e) == 2]
            state, version = j.get("state"), j.get("version", "?")
        except (OSError, ValueError, AttributeError):
            return _UNREADABLE.format("its journal cannot be read")
        if state == "committed":
            try:
                _finish_commit(target)
            except OSError:
                return f"The update to {version} is installed but could not finish tidying up."
            return f"The update to {version} was completed."
        if _rollback(target, entries):
            return (f"The update to {version} was interrupted. The previous version was "
                    "restored; your settings and data were not touched.")
        return (f"The update to {version} was interrupted and could not be fully undone yet. "
                "SK will try again at the next start.")
    finally:
        if lock is not None:
            lock.release()


def recover_at_start() -> None:
    """Called first thing by app.py. Never stops SK from starting."""
    global LAST_RECOVERY
    try:
        LAST_RECOVERY = recover()
    except Exception as exc:                  # pragma: no cover - last resort
        LAST_RECOVERY = f"An interrupted update could not be checked ({type(exc).__name__})."


class PackageError(OSError):
    pass


def update_packages(changed: List[str], target: Optional[str] = None) -> None:
    """New Python packages are installed only if requirements.txt changed.
    Raises PackageError if pip fails (the update is then undone)."""
    if "requirements.txt" not in [c.replace(os.sep, "/") for c in changed]:
        return
    target = target or app_dir()
    res = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                          os.path.join(target, "requirements.txt")], cwd=target,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if res.returncode != 0:
        raise PackageError(f"installing the required Python packages failed (pip exit code "
                           f"{res.returncode})")


def _same(a: str, b: str) -> bool:
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


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
