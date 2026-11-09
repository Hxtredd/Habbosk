"""Version-testing record: runs the whole test suite on THIS machine and writes
one record (Markdown + JSON) that says exactly what was tested and how it went.

    python tools/version_record.py [--milestone B7] [--version 0.7-dev]
                                   [--breaks "163/163 caught (breaks6)"] [--out DIR]

Same script on Linux, in GitHub Actions and on Windows (VERSION_TEST.bat). The
platform is the one the tests really ran on - a record never claims another.
Your data is never used: the suite's data folder is a new temporary folder.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def commit() -> str:
    """git rev-parse in a clone; else the COMMIT file, which `git archive` and
    GitHub's zip downloads fill in (export-subst)."""
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                           text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                   capture_output=True, text=True, timeout=20).stdout.strip()
            return r.stdout.strip() + (" (+ uncommitted changes)" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with open(os.path.join(ROOT, "COMMIT"), encoding="utf-8") as fh:
            c = fh.read().strip()
        if c and not c.startswith("$Format"):
            return c.split()[0] + " (from a downloaded copy)"
    except OSError:
        pass
    return "unknown"


def brain_fingerprint() -> str:
    """The same fingerprint BRAIN_WINDOWS_CHECK prints."""
    h = hashlib.sha256()
    brain = os.path.join(ROOT, "puzzle_assistant", "brain")
    for name in sorted(os.listdir(brain)):
        if name.endswith(".py"):
            with open(os.path.join(brain, name), "rb") as fh:
                h.update(name.encode() + b"\0" + fh.read().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def platform_name() -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return f"Linux (GitHub Actions, {platform.platform()})"
    if sys.platform == "win32":
        return f"Windows ({platform.platform()})"
    return f"{platform.system()} ({platform.platform()})"


def run_suite():
    data = tempfile.mkdtemp(prefix="sk_version_record_")
    env = dict(os.environ, SK_ASSISTANT_DATA=data)
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                           cwd=ROOT, capture_output=True, text=True, timeout=3600, env=env)
    finally:
        shutil.rmtree(data, ignore_errors=True)
    out = r.stderr + r.stdout
    ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", out, re.M)
    counts = {k: int(v) for k, v in re.findall(r"(failures|errors|skipped|expected failures)=(\d+)",
                                                 (re.findall(r"^(?:OK|FAILED).*$", out, re.M)
                                                  or [""])[-1])}
    total = int(ran.group(1)) if ran else 0
    failed, errors, skipped = (counts.get("failures", 0), counts.get("errors", 0),
                               counts.get("skipped", 0))
    bad = [ln for ln in out.splitlines() if ln.startswith(("FAIL:", "ERROR:"))]
    return {"exit_code": r.returncode, "ran": total, "passed": total - failed - errors - skipped,
            "failed": failed, "errors": errors, "skipped": skipped,
            "seconds": float(ran.group(2)) if ran else None, "failing_tests": bad,
            "ok": r.returncode == 0 and total > 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--milestone", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("--breaks", default="not run in this record")
    ap.add_argument("--out", default=ROOT)
    a = ap.parse_args(argv)
    res = run_suite()
    rec = {"date_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "commit": commit(), "milestone": a.milestone, "version": a.version,
           "brain_fingerprint": brain_fingerprint(), "platform": platform_name(),
           "python": platform.python_version(), "suite": res, "deliberate_breaks": a.breaks,
           "approved": "no (approval is the owner's, recorded in docs/testing/versions.md)"}
    s = res
    md = [f"### {rec['commit'][:12]} - {a.milestone or '?'} {a.version}".rstrip(),
          f"- Commit: {rec['commit']}",
          f"- Date (UTC): {rec['date_utc']}",
          f"- Platform: {rec['platform']}, Python {rec['python']}",
          f"- Brain code fingerprint: {rec['brain_fingerprint']}",
          f"- Whole suite: {'PASSED' if s['ok'] else 'FAILED'} - ran {s['ran']}, passed "
          f"{s['passed']}, failed {s['failed']}, errors {s['errors']}, skipped {s['skipped']}",
          f"- Deliberate breaks: {a.breaks}",
          f"- Approved: {rec['approved']}"]
    if s["failing_tests"]:
        md.append(f"- All {len(s['failing_tests'])} failing tests:")
        md += [f"  - {t}" for t in s["failing_tests"]]
    text = "\n".join(md) + "\n"
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "version_test_record.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(a.out, "version_test_record.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    print(text)
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
