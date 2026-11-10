"""Step 3: deliberate-break tests. Each break changes ONE line of a COPY of the Brain code so
that a protection is switched off, then runs the tests that should notice. A break the tests
notice is CAUGHT (good). One they do not notice is MISSED - unless it is listed as an
'equivalent' change (the protection is duplicated elsewhere, so removing it changes nothing
observable; each is explained in breaks.json).

The verified source is never edited: every break runs in a fresh copy inside test_area."""
import json
import os
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import verify_package  # noqa: E402


def main():
    ok, head = verify_package.verify(quiet=False)
    if not ok:
        return 1
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    breaks = json.load(open(os.path.join(common.HERE, "breaks.json"), encoding="utf-8"))
    if only:
        breaks = [b for b in breaks if any(o.lower() in b["name"].lower() for o in only)]
    env = common.isolated_environment("breaks")
    say_log = []

    def say(s=""):
        print(s, flush=True)
        say_log.append(s)

    say(common.python_banner())
    say(f"{len(breaks)} deliberate breaks; each runs the matching tests on a broken COPY.")
    say("")
    work = os.path.join(common.AREA, "break_work")
    caught = missed = setup = unexpected = 0
    t0 = time.time()
    for i, b in enumerate(breaks, 1):
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        for d in ("puzzle_assistant", "tests"):
            shutil.copytree(os.path.join(common.SOURCE, d), os.path.join(work, d),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        p = os.path.join(work, *b["file"].split("/"))
        text = open(p, encoding="utf-8", newline="").read()
        if text.count(b["old"]) != 1:
            say(f"[{i:2d}/{len(breaks)}] SETUP-ERROR {b['name']}: expected 1 match, found {text.count(b['old'])}")
            setup += 1
            continue
        open(p, "w", encoding="utf-8", newline="").write(text.replace(b["old"], b["new"]))
        try:
            r = subprocess.run([sys.executable, "-I", os.path.join(common.HERE, "child_tests.py"),
                                work] + b["tests"], cwd=work, env=env, capture_output=True,
                               text=True, timeout=900)
            was_caught = r.returncode != 0
            first = [l for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))][:1]
        except subprocess.TimeoutExpired:
            was_caught, first = True, ["(timed out)"]
        expected_equiv = bool(b.get("equivalent"))
        if was_caught:
            caught += 1
            say(f"[{i:2d}/{len(breaks)}] CAUGHT  {b['name']}")
        elif expected_equiv:
            say(f"[{i:2d}/{len(breaks)}] same-as-before (equivalent, expected)  {b['name']}\n"
                f"          why: {b['equivalent']}")
            missed += 1
        else:
            say(f"[{i:2d}/{len(breaks)}] MISSED  {b['name']}   <-- a protection is not tested")
            missed += 1
            unexpected += 1
    shutil.rmtree(work, ignore_errors=True)
    again, _ = verify_package.verify(quiet=True)
    say("")
    say(f"Breaks: {len(breaks)}; caught {caught}; not noticed {missed} "
        f"({missed - unexpected} documented equivalents, {unexpected} UNEXPECTED); "
        f"setup errors {setup}; {time.time() - t0:.0f}s")
    say("Source unchanged after the run: " + ("yes" if again else "NO"))
    passed = unexpected == 0 and setup == 0 and again
    say("RESULT: " + ("PASS" if passed else "FAIL") + "   deliberate breaks, commit " + head["source commit"])
    say("Platform: " + common.python_banner())
    try:
        with open(os.path.join(common.PKG, "privacy_breaks_report.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(say_log) + "\n")
        print("Report written to: " + os.path.join(common.PKG, "privacy_breaks_report.txt"))
    except OSError as e:
        print("could not write the report:", e)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
