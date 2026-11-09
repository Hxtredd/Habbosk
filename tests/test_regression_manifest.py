"""The permanent regression suite (tests/regression_manifest.json): every
listed module exists, loads, and keeps at least its floor of tests - a new
milestone cannot silently delete or skip away an earlier capability."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

MANIFEST = os.path.join(HERE, "regression_manifest.json")


def floors():
    with open(MANIFEST, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for group, mods in data["groups"].items():
        for m, n in mods.items():
            out[m] = (group, n)
    return out


class RegressionSuite(unittest.TestCase):
    def test_every_listed_module_keeps_its_floor(self):
        loader = unittest.TestLoader()
        for mod, (group, floor) in floors().items():
            with self.subTest(module=mod, group=group):
                self.assertTrue(os.path.isfile(os.path.join(HERE, mod + ".py")),
                                f"{mod} is missing")
                n = loader.loadTestsFromName("tests." + mod).countTestCases()
                self.assertGreaterEqual(n, floor, f"{mod}: {n} tests, floor {floor}")

    def test_every_brain_test_module_is_listed(self):
        listed = set(floors())
        brain = {f[:-3] for f in os.listdir(HERE)
                 if f.startswith("test_brain") and f.endswith(".py")}
        self.assertEqual(sorted(brain - listed), [],
                         "new Brain test modules must be added to regression_manifest.json")

    def test_floors_are_positive(self):
        for mod, (_, floor) in floors().items():
            self.assertGreater(floor, 0, mod)


if __name__ == "__main__":
    unittest.main()
