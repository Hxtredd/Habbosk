"""Make tests/fixtures/b52_library.zip: a REAL B5.2-format library, written by the
B5.2 code itself (a checkout of 3d75dd4), with the answers that code gave.

    git worktree add /tmp/b52 3d75dd43e3cecd04eb98bf688cde34b387bb3d8f
    python tests/b6_make_b52_fixture.py /tmp/b52

Synthetic data only (tests/b52_world.py of that checkout: five people, a
moderator, privacy markers, one approved proposal). The keys are TEST keys,
stored with the fixture on purpose.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
HK, LK = "11" * 32, "22" * 32
QUESTIONS = ["Who maintains the Lantern Garden?", "Who maintains the Secret Cellar?",
             "What is the room ID of the Hidden Vault?", "Who maintains the Hidden Vault?",
             "The Old Pier room ID", "Lantern Garden, who looks after it?"]

if __name__ == "__main__":
    if sys.argv[1] == "--build":
        root, d = sys.argv[2], sys.argv[3]
        sys.path.insert(0, os.path.join(root, "tests"))
        sys.path.insert(0, root)
        import puzzle_assistant.brain as pb
        assert os.path.abspath(pb.__file__).startswith(os.path.abspath(root)), pb.__file__
        import b52_world as W
        w = W.World(d, bytes.fromhex(HK), bytes.fromhex(LK)).build()
        answers = {}
        for who in ("ana", "luis", "eve", "kim"):
            s = w.open(who)
            answers[who] = {q: [r.category, r.reason, r.value] for q in QUESTIONS
                            for r in [s.respond(q)]}
            s.close()
        info = {"ids": w.ids, "answers": answers, "made_by": subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
            text=True).stdout.strip(), "hk": HK, "lk": LK,
            "markers": W.PERSONAL, "granted": W.GRANTED}
        w.lib.close()
        print(json.dumps(info))
        sys.exit(0)
    root = sys.argv[1]
    d = tempfile.mkdtemp(prefix="sk_b52_fixture_")
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--build", root, d],
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise SystemExit(r.stderr[-3000:])
        info = json.loads(r.stdout)
        out = os.path.join(HERE, "fixtures", "b52_library.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for dd, _, names in os.walk(os.path.join(d, "brain")):
                for n in sorted(names):
                    p = os.path.join(dd, n)
                    z.write(p, os.path.relpath(p, d))
            z.writestr("fixture.json", json.dumps(info, indent=1, sort_keys=True))
        print("written", out, os.path.getsize(out), "bytes, made by", info["made_by"])
    finally:
        shutil.rmtree(d, ignore_errors=True)
