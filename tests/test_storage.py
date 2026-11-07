"""Stage 4 part 1: memory in the user profile, merging older memories, the
starter questions, importing a found habbosk.db."""
import os
import sys
import tempfile
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant import storage  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.ocr import find_tesseract  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import Answer, Question  # noqa: E402

TROYA = Question("ADIVINANZA", "Parezco un regalo, pero escondía soldados griegos para conquistar "
                 "una ciudad. ¿Qué soy?", ["La espada de Aquiles", "El escudo de Esparta",
                                           "El barco de Ulises", "El caballo de Troya"])
NPC = Question("¿QUIÉN SOY?", "Soy un asistente NPC", ["Rubén", "Mateo", "Tomás", "Leo"])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.old = os.path.join(self.root, "program", "data")
        self.new = os.path.join(self.root, "appdata")
        os.makedirs(self.old)
        self.saved = {k: getattr(storage, k) for k in
                      ("LEGACY_DATA_DIR", "DATA_DIR", "CONFIG_PATH", "LEGACY_CONFIG_PATH")}
        storage.LEGACY_DATA_DIR = self.old
        storage.DATA_DIR = self.new
        storage.CONFIG_PATH = os.path.join(self.new, "config.json")
        storage.LEGACY_CONFIG_PATH = os.path.join(self.root, "program", "config.json")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(storage, k, v)

    def test_old_memory_is_merged_once(self):
        old = Memory(os.path.join(self.old, "habbosk.db"))
        old.save(TROYA, Answer(choice="El caballo de Troya", source="game", verified=True))
        old.save(NPC, Answer(choice="Leo", source="game", verified=True))
        old.close()
        with open(storage.LEGACY_CONFIG_PATH, "w") as fh:
            fh.write('{"text_scale": 1.3}')
        with open(os.path.join(self.old, "keys.bin"), "wb") as fh:
            fh.write(b"PLAIN:e30=")
        new = Memory(os.path.join(self.new, "habbosk.db"))
        try:
            # the new memory already knows one of them (no duplicate, no loss)
            new.save(NPC, Answer(choice="Leo", source="game", verified=True))
            notes = storage.migrate_legacy(new)
            self.assertEqual(new.stats()["questions"], 2)
            self.assertEqual(new.lookup(TROYA).answer.choice, "El caballo de Troya")
            self.assertTrue(os.path.exists(os.path.join(self.old, "habbosk.db.merged")))
            self.assertTrue(os.path.exists(storage.CONFIG_PATH))
            self.assertTrue(os.path.exists(os.path.join(self.new, "keys.bin")))
            self.assertTrue(any("merged" in n for n in notes))
            self.assertEqual(storage.migrate_legacy(new), [])          # not twice
        finally:
            new.close()

    def test_seed_once_and_import_export(self):
        m = Memory(os.path.join(self.new, "m.db"))
        try:
            self.assertEqual(storage.add_seed(m), 12)
            self.assertEqual(storage.add_seed(m), 0)
            out = os.path.join(self.root, "export.json")
            m.export_json(out)
            other = Memory(os.path.join(self.root, "other.db"))
            c = other.merge_from(out)                   # 'Import…' of an export
            self.assertEqual(c["new"], 12)
            other.close()
            c = m.merge_from(os.path.join(self.root, "other.db"))   # 'Import…' of a .db
            self.assertEqual(c["new"], 0)
        finally:
            m.close()


@unittest.skipUnless(find_tesseract(Config()), "Tesseract not installed")
class SeedMatchesScreensTests(unittest.TestCase):
    """The starter questions must be found when the real screens are read."""

    def test_seed_answers_recorded_screens(self):
        from puzzle_assistant.ocr import Tesseract
        from puzzle_assistant.qa.screen import locate_question, read_question
        m = Memory(os.path.join(tempfile.mkdtemp(), "m.db"))
        ocr = Tesseract(Config())
        try:
            storage.add_seed(m)
            want = {"adivinanza_open": "El raspadito", "letras_open": "V",
                    "pista_open": "70", "quien_soy_answered": "Leo",
                    "error_answered": "Con :dar puedes entregar dinero que tienes en el banco."}
            for name, answer in want.items():
                img = cv2.imread(os.path.join(HERE, "fixtures", "questions", name + ".png"))
                q = read_question(img, locate_question(img), ocr).question
                hit = m.lookup(q)
                self.assertIsNotNone(hit, name)
                self.assertEqual(hit.stored.choice, answer, name)
            for name in ("ordenar_peces_open", "parejas_animales_open"):
                img = cv2.imread(os.path.join(HERE, "fixtures", "questions", name + ".png"))
                self.assertIsNotNone(m.lookup(read_question(img, locate_question(img), ocr).question), name)
        finally:
            m.close()


if __name__ == "__main__":
    unittest.main()
