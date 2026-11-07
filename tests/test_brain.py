"""SK Brain B1: knowledge library schema and rules, API, read-only adapters."""
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain.store import BrainError  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import Answer, Question  # noqa: E402
from puzzle_assistant.world_atlas.db import Atlas  # noqa: E402


def tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


def file_hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class LibraryRulesTests(unittest.TestCase):
    def setUp(self):
        self.b = Brain(tmp("knowledge.db"))

    def tearDown(self):
        self.b.close()

    def test_new_claims_are_never_verified_or_shared_on_creation(self):
        c = self.b.learn("El Gran Casino está junto al Spa.", "imported", "Gran Casino", "lugares")
        self.assertEqual(self.b.claim(c)["status"], "submitted")
        self.assertEqual(self.b.claim(c)["scope"], "personal")
        with self.assertRaises(BrainError):
            self.b.store.add_claim("x", "imported", status="verified")
        with self.assertRaises(BrainError):
            self.b.store.add_claim("x", "imported", scope="shared")
        with self.assertRaises(BrainError):
            self.b.learn("x", "rumour")                      # unknown origin

    def test_repeating_a_claim_never_verifies_it(self):
        c = self.b.learn("La mina está en Avenida Crisol.", "user", "Mina")
        for name in ("Ana", "Luis", "Marta"):                  # three people say it
            who = self.b.store.add_contributor(name)
            self.b.add_evidence(c, "assertion", by=who)
        s = self.b.explain(c)["support"]
        self.assertEqual((s["direct"], s["assertions"], s["contributors"]), (0, 3, 3))
        with self.assertRaises(BrainError):
            self.b.review(c, "verified")
        self.assertEqual(self.b.claim(c)["status"], "submitted")

    def test_same_material_counts_once_and_direct_evidence_allows_verification(self):
        c = self.b.learn("El Gran Casino es la sala 12411.", "observed", "Gran Casino")
        shot1 = self.b.add_source("screenshot", title="casino", content_hash="abc")
        shot2 = self.b.add_source("screenshot", title="casino (same file, other user)",
                                  content_hash="abc", by=self.b.store.add_contributor("Ana"))
        self.b.add_evidence(c, "direct", shot1, "share panel URL")
        self.b.add_evidence(c, "confirmation", shot2)
        s = self.b.explain(c)["support"]
        self.assertEqual((s["direct"], s["confirmations"]), (1, 0))   # same picture: once
        self.b.review(c, "verified", note="URL readable in the screenshot")
        ev = self.b.explain(c)["events"]
        self.assertEqual(ev[-1]["event"], "reviewed")
        self.assertEqual((ev[-1]["old_status"], ev[-1]["new_status"]), ("submitted", "verified"))

    def test_ai_suggestion_needs_real_evidence(self):
        c = self.b.learn("Leo es el asistente NPC.", "ai", "Leo")
        with self.assertRaises(BrainError):
            self.b.review(c, "verified")
        with self.assertRaises(BrainError):
            self.b.add_evidence(c, "direct")                 # direct evidence needs a source
        game = self.b.add_source("game_observation", title="quiz reveal")
        self.b.add_evidence(c, "direct", game)
        self.b.review(c, "verified")
        self.assertEqual(self.b.claim(c)["origin"], "ai")    # origin is never rewritten

    def test_correction_keeps_the_original(self):
        c = self.b.learn("El banco abre a las 8.", "imported", "Banco")
        v = self.b.correct(c, "El banco abre a las 9.", reason="guide page updated")
        self.assertEqual(v, 2)
        e = self.b.explain(c)
        self.assertEqual(e["claim"]["text"], "El banco abre a las 9.")
        self.assertEqual(e["claim"]["status"], "awaiting_verification")
        self.assertEqual(e["versions"][0]["text"], "El banco abre a las 8.")
        self.assertEqual(e["versions"][0]["reason"], "guide page updated")
        self.assertEqual([x["event"] for x in e["events"]], ["created", "corrected"])

    def test_contradiction_flags_both_and_keeps_both(self):
        a = self.b.learn("El taxi cuesta $5.", "user", "taxi")
        b = self.b.learn("El taxi es gratis.", "user", "taxi")
        self.b.link(b, a, "contradicts")
        self.assertEqual(self.b.claim(a)["status"], "conflicting")
        self.assertEqual(self.b.claim(b)["status"], "conflicting")
        self.assertEqual(len(self.b.store.claims(subject="taxi")), 2)

    def test_private_evidence_blocks_sharing(self):
        c = self.b.learn("Mi contraseña del banco es…", "user", "banco")
        priv = self.b.add_source("screenshot", title="my chat", private=True)
        self.b.add_evidence(c, "direct", priv)
        with self.assertRaises(BrainError):
            self.b.propose_share(c)
        self.assertEqual(self.b.claim(c)["scope"], "personal")
        ok = self.b.learn("El Spa está en Avenida Espumia.", "imported", "Spa")
        self.b.propose_share(ok)
        self.assertEqual(self.b.claim(ok)["scope"], "shared_candidate")
        self.assertEqual(self.b.explain(ok)["events"][-1]["event"], "share_proposed")

    def test_contributors_and_schema(self):
        self.assertTrue(self.b.local_user)
        self.assertEqual(self.b.store.schema_version, 1)
        with self.assertRaises(BrainError):
            self.b.learn("x", "user", by="nobody")           # unknown contributor
        # reopening keeps the same local user
        path = self.b.store.path
        uid = self.b.local_user
        self.b.close()
        self.b = Brain(path)
        self.assertEqual(self.b.local_user, uid)


class AdapterTests(unittest.TestCase):
    """The quiz memory and the World Atlas are read, never written."""

    def setUp(self):
        self.qpath, self.apath = tmp("habbosk.db"), tmp("atlas.db")
        m = Memory(self.qpath)
        m.save(Question("¿QUIÉN SOY?", "Soy un asistente NPC", ["Rubén", "Mateo", "Leo"]),
               Answer(choice="Leo", explanation="Leo cubre el turno del gimnasio.",
                      source="game", verified=True))
        m.save(Question("PREGUNTA", "¿Capital de Francia?", ["Roma", "París"]),
               Answer(choice="París", source="ai"))
        m.close()
        a = Atlas(self.apath)
        a.import_input()
        a.record_visit(13, "Ayuntamiento de Ensenada", None, None, "start")
        a.record_visit(41, "Avenida Alameda", None, 13, "walk")
        a.close()
        self.before = (file_hash(self.qpath), file_hash(self.apath))
        self.b = Brain(tmp("knowledge.db"), self.qpath, self.apath)

    def tearDown(self):
        self.b.close()

    def test_results_from_both_modules_with_their_meaning(self):
        hits = self.b.search("asistente NPC Leo gimnasio")
        q = [h for h in hits if h.module == "quiz_memory"]
        self.assertEqual((q[0].status, q[0].origin), ("verified", "observed"))
        self.assertIn("Leo", q[0].text)
        ai = [h for h in self.b.search("capital Francia") if h.module == "quiz_memory"][0]
        self.assertEqual((ai.status, ai.origin), ("submitted", "ai"))   # AI stays unverified
        casino = [h for h in self.b.search("Gran Casino") if h.module == "world_atlas"]
        self.assertTrue(any("12411" in h.text for h in casino))
        walk = [h for h in self.b.search("Ayuntamiento Alameda") if h.record_id == "walk:13-41"]
        self.assertEqual(walk[0].status, "submitted")

    def test_adapters_never_change_the_files(self):
        for q in ("Leo", "Gran Casino", "Avenida Alameda", "nada que ver"):
            self.b.search(q)
        self.b.ask("¿Quién es Leo?")
        self.b.close()
        self.assertEqual((file_hash(self.qpath), file_hash(self.apath)), self.before)
        ro = Brain(tmp("k.db"), self.qpath, self.apath)
        with self.assertRaises(sqlite3.OperationalError):          # opened read-only
            ro.sources[0].ro.db.execute("DELETE FROM questions")
        ro.close()
        self.b = Brain(tmp("k2.db"))

    def test_missing_files_are_not_created(self):
        q, a = tmp("none.db"), tmp("none2.db")
        b = Brain(tmp("k.db"), q, a)
        self.assertEqual(b.search("Leo"), [])
        b.close()
        self.assertFalse(os.path.exists(q) or os.path.exists(a))

    def test_ask_does_not_invent_answers_yet(self):
        r = self.b.ask("¿Quién es Leo?")
        self.assertFalse(r.answered)
        self.assertEqual(r.basis, "insufficient")
        self.assertTrue(r.hits)


class IndependenceTests(unittest.TestCase):
    def test_brain_does_not_load_the_interface(self):
        code = ("import sys; sys.path.insert(0, %r); import puzzle_assistant.brain as b; "
                "b.Brain(%r).close(); "
                "print('tkinter' in sys.modules, 'puzzle_assistant.ui' in sys.modules)") % (
                    ROOT, tmp("k.db"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.stdout.strip(), "False False", out.stderr)


if __name__ == "__main__":
    unittest.main()
