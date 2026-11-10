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
    if not breaks:
        print("No break matches that filter; nothing was run.")
        return 1
    partial = bool(only)
    env = common.isolated_environment("breaks")
    say_log = []
    w = common.python_warning()

    def say(s=""):
        print(s, flush=True)
        say_log.append(s)

    say(common.python_banner())
    if w:
        say(w)
    common.block_network()
    if not common.network_selftest():
        say("The network block is not working - nothing was run.")
        return 1
    say("network block self-test: ok (parent and every test process)")
    say(f"{len(breaks)} deliberate breaks; each runs the matching tests on a broken COPY.")
    say("")
    work = os.path.join(common.AREA, "break_work")
    shutil.rmtree(work, ignore_errors=True)

    def fresh_copy():
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        for d in ("puzzle_assistant", "tests"):
            shutil.copytree(os.path.join(common.SOURCE, d), os.path.join(work, d),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                            dirs_exist_ok=True)

    def run_tests(tests):
        r = subprocess.run([sys.executable, "-I", "-B", os.path.join(common.HERE, "child_tests.py"),
                            work] + tests, cwd=work, env=env, capture_output=True, text=True,
                           timeout=900)
        ran = [l for l in r.stderr.splitlines() if l.startswith("Ran ")]
        return r, (int(ran[0].split()[1]) if ran else 0)

    # BASELINE: the unbroken copy must pass, or "caught" would mean nothing
    env = common.isolated_environment("breaks")
    fresh_copy()
    base_tests = sorted({t for b in breaks for t in b["tests"]})
    r0, n0 = run_tests(base_tests)
    say(f"baseline (no break): {n0} tests, " + ("all pass" if r0.returncode == 0 and n0 else "FAILED"))
    if r0.returncode != 0 or n0 == 0:
        say("The unbroken code does not pass here, so no break result would be meaningful.")
        for l in r0.stderr.splitlines()[-12:]:
            say("    " + l)
        say("RESULT: FAIL   deliberate breaks, commit " + head["source commit"])
        return 1
    say("")
    caught = missed = setup = unexpected = 0
    t0 = time.time()
    for i, b in enumerate(breaks, 1):
        env = common.isolated_environment("breaks")      # fresh data, temp and home per break
        fresh_copy()
        p = os.path.join(work, *b["file"].split("/"))
        text = open(p, encoding="utf-8", newline="").read()
        if text.count(b["old"]) != 1:
            say(f"[{i:2d}/{len(breaks)}] SETUP-ERROR {b['name']}: expected 1 match, found {text.count(b['old'])}")
            setup += 1
            continue
        broken_text = text.replace(b["old"], b["new"])
        try:
            compile(broken_text, p, "exec")
        except SyntaxError as e:
            say(f"[{i:2d}/{len(breaks)}] SETUP-ERROR {b['name']}: the broken code does not compile ({e.msg})")
            setup += 1
            continue
        open(p, "w", encoding="utf-8", newline="").write(broken_text)
        try:
            r, ran = run_tests(b["tests"])
            fails = [l for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
            kind = common.error_kind(r.stderr)
            # caught = a test ran and its own check failed (an assertion or our NotShareable
            # refusal); a crash of the edited code (syntax / name / import error) is not a catch
            was_caught = r.returncode != 0 and ran > 0 and \
                (kind == "assertion" or (kind == "other" and bool(b.get("allow_other"))))
            if r.returncode != 0 and not was_caught:
                say(f"          (failed with kind '{kind}', not an assertion: not counted as caught)")
            first = fails[:1]
        except subprocess.TimeoutExpired:
            was_caught, first = False, ["(timed out - not counted as caught)"]
        expected_equiv = bool(b.get("equivalent"))
        if was_caught:
            caught += 1
            say(f"[{i:2d}/{len(breaks)}] CAUGHT  {b['name']}\n          by: " + (first[0].split(" (")[0][:90] if first else "?"))
        elif expected_equiv:
            say(f"[{i:2d}/{len(breaks)}] same-as-before (equivalent, expected)  {b['name']}\n"
                f"          why: {b['equivalent']}")
            missed += 1
        else:
            say(f"[{i:2d}/{len(breaks)}] MISSED  {b['name']}   <-- a protection is not tested")
            missed += 1
            unexpected += 1
    shutil.rmtree(work, ignore_errors=True)
    odd = common.remove_compiled(common.SOURCE)
    if odd:
        say("unexpected files in __pycache__: " + ", ".join(odd[:5]))
    again, _ = verify_package.verify(quiet=True)
    say("")
    say(f"Breaks: {len(breaks)}; caught {caught}; not noticed {missed} "
        f"({missed - unexpected} documented equivalents, {unexpected} UNEXPECTED); "
        f"setup errors {setup}; {time.time() - t0:.0f}s")
    say("Source unchanged after the run: " + ("yes" if again else "NO"))
    passed = unexpected == 0 and setup == 0 and again and not odd
    if partial:
        say("PARTIAL RUN (a filter was given): this is not the full result.")
    say("RESULT: " + ("PASS" if passed else "FAIL") + ("-PARTIAL" if partial and passed else "") + "   deliberate breaks, commit " + head["source commit"])
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
