"""One-click updates: version check, verified download, in-place install
that keeps .venv / settings and backs up replaced files."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant import __version__, updater  # noqa: E402


def make_zip(files: dict, top="SK_Assistant_v9.9") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(f"{top}/{name}", data)
    return buf.getvalue()


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class UpdaterTests(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.newer("2.10.0", "2.9.1"))
        self.assertFalse(updater.newer("2.6.0", "2.6.0"))
        self.assertFalse(updater.newer("2.5", "2.6.0"))

    def test_check(self):
        latest = {"version": "99.0.0", "zip": "dist/SK_Assistant_v99.0.zip", "sha256": "AB",
                  "size": 10, "notes": "x"}
        rel = updater.check(lambda url: json.dumps(latest).encode())
        self.assertEqual((rel.version, rel.sha256), ("99.0.0", "ab"))
        self.assertTrue(rel.url.endswith("/dist/SK_Assistant_v99.0.zip"))
        latest["version"] = __version__
        self.assertIsNone(updater.check(lambda url: json.dumps(latest).encode()))

    def test_download_is_verified(self):
        data = make_zip({"puzzle_assistant/__init__.py": "x"})
        good = updater.Release("9.9", "https://example.invalid/u.zip", hashlib.sha256(data).hexdigest(), len(data))
        path = updater.download(good, opener=lambda req, timeout: FakeResp(data))
        self.assertEqual(open(path, "rb").read(), data)
        os.remove(path)
        bad = updater.Release("9.9", "https://example.invalid/u.zip", "0" * 64, len(data))
        with self.assertRaises(ValueError):
            updater.download(bad, opener=lambda req, timeout: FakeResp(data))

    def test_install_in_place(self):
        target = tempfile.mkdtemp()
        os.makedirs(os.path.join(target, "puzzle_assistant"))
        os.makedirs(os.path.join(target, ".venv"))
        for rel, txt in (("puzzle_assistant/__init__.py", "old"), ("README.txt", "same"),
                         (".venv/python.exe", "venv"), ("config.json", "{}")):
            with open(os.path.join(target, rel), "w") as fh:
                fh.write(txt)
        z = os.path.join(tempfile.mkdtemp(), "u.zip")
        with open(z, "wb") as fh:
            fh.write(make_zip({"puzzle_assistant/__init__.py": "new", "README.txt": "same",
                               "puzzle_assistant/extra.py": "added", ".venv/python.exe": "evil",
                               "config.json": "{\"reset\": 1}"}))
        changed = updater.install(z, target)
        self.assertEqual(sorted(changed), [os.path.normpath("puzzle_assistant/__init__.py"),
                                           os.path.normpath("puzzle_assistant/extra.py")])
        read = lambda p: open(os.path.join(target, p)).read()  # noqa: E731
        self.assertEqual(read("puzzle_assistant/__init__.py"), "new")
        self.assertEqual(read(".venv/python.exe"), "venv")       # never touched
        self.assertEqual(read("config.json"), "{}")
        self.assertEqual(read("_previous/puzzle_assistant/__init__.py"), "old")   # backup
        self.assertFalse(os.path.exists(z))                       # download removed

    def test_refuses_unsafe_or_foreign_zips(self):
        target = tempfile.mkdtemp()
        for files in ({"../escape.txt": "x"}, {"something/else.txt": "x"}):
            z = os.path.join(tempfile.mkdtemp(), "u.zip")
            with open(z, "wb") as fh:
                fh.write(make_zip(files, top="x"))
            with self.assertRaises(ValueError):
                updater.install(z, target)
        self.assertEqual(os.listdir(target), [])


if __name__ == "__main__":
    unittest.main()
