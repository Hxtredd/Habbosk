"""Temporary SK Brain test bench: its automatic checks (no window needed)."""
import hashlib
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.devtools.brain_checks import readonly_check, scenario_checks  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import Answer, Question  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402


def sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class BenchChecksTests(unittest.TestCase):
    def test_all_scenario_checks_pass_and_leave_nothing_behind(self):
        before = set(os.listdir(tempfile.gettempdir()))
        res = scenario_checks()
        self.assertEqual(len(res), 8)
        for name, ok, detail in res:
            self.assertTrue(ok, f"{name}: {detail}")
        left = {d for d in set(os.listdir(tempfile.gettempdir())) - before
                if d.startswith("sk_brain_check_")}
        self.assertEqual(left, set())                     # throwaway database removed

    def test_readonly_check_on_real_files(self):
        d = tempfile.mkdtemp()
        q, a = os.path.join(d, "habbosk.db"), os.path.join(d, "atlas.db")
        m = Memory(q)
        m.save(Question("PREGUNTA", "¿Quién es Leo?", ["Leo", "Rubén"]),
               Answer(choice="Leo", source="game", verified=True))
        m.close()
        at = Atlas(a)
        at.import_input()
        at.close()
        h = (sha(q), sha(a))
        b = Brain(os.path.join(d, "knowledge.db"), q, a)
        res = readonly_check(b)
        b.close()
        self.assertEqual([ok for _, ok, _ in res], [True, True])
        self.assertTrue(all("unchanged" in det for _, _, det in res))
        self.assertEqual((sha(q), sha(a)), h)

    def test_bench_module_stays_out_of_the_brain(self):
        import puzzle_assistant.brain as brain_pkg
        src = open(brain_pkg.__file__).read()
        self.assertNotIn("devtools", src)                 # removable: the Brain doesn't need it


if __name__ == "__main__":
    unittest.main()
