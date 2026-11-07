"""Stage 4 part 2: AI answers. No real network: the web requests are mocked."""
import json
import os
import queue
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant import secrets  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.qa import ai  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import ORDER, PAIRS, Answer, Question  # noqa: E402

Q = Question("PREGUNTA", "¿Cuál es la capital de Francia?", ["Madrid", "París", "Roma"])
MODELS = {"models": [
    {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.6-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]}]}


def gemini_reply(obj, tin=50, tout=20):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}],
            "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout}}


class FakeHttp:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.saved_keys = secrets.KEYS_PATH
        secrets.KEYS_PATH = os.path.join(d, "keys.bin")
        self.mem = Memory(os.path.join(d, "m.db"))
        self.cfg = Config()
        self.cfg.ai_provider = "gemini"
        secrets.set_key("ai:gemini", "test-key")

    def tearDown(self):
        self.mem.close()
        secrets.KEYS_PATH = self.saved_keys


class ParseTests(unittest.TestCase):
    def test_choice_order_pairs(self):
        a = ai.parse_reply('{"answer": "b", "explanation": "x", "confidence": 0.9}', Q)
        self.assertEqual((a.choice, a.source, a.confidence), ("París", "ai", 0.9))
        o = Question("ORDENAR", "Ordena", ["c", "a", "b"], ORDER)
        self.assertEqual(ai.parse_reply('{"order": ["B","C","A"]}', o).order, ["a", "b", "c"])
        p = Question("PAREJAS", "Une", ["perro", "gato"], PAIRS, right=["miau", "guau"])
        self.assertEqual(ai.parse_reply('```json\n{"pairs": {"A": "2", "B": 1}}\n```', p).pairs,
                         {"perro": "guau", "gato": "miau"})

    def test_bad_replies_are_refused(self):
        for bad in ('{"answer": "D"}', "no idea", '{"order": ["A","A","B"]}'):
            with self.assertRaises(ValueError):
                ai.parse_reply(bad, Question("ORDENAR", "x", ["a", "b", "c"], ORDER)
                               if "order" in bad else Q)

    def test_prompt_has_letters_and_negation_warning(self):
        p = ai.build_prompt(Question("ENCUENTRA EL ERROR", "¿Qué frase tiene un error?",
                                     ["uno", "dos"], clues=["• pista"]))
        self.assertIn("A. uno", p)
        self.assertIn("Clue: • pista", p)
        self.assertIn("'error'", ai.SYSTEM)


class GeminiTests(Base):
    def test_picks_newest_stable_flash_lite_and_answers(self):
        http = FakeHttp([(200, MODELS), (200, gemini_reply({"answer": "B", "confidence": 0.95}))])
        r = ai.AIAnswerer(self.cfg, self.mem, http).answer(Q)
        self.assertEqual(r.answer.choice, "París")
        self.assertEqual(r.model, "gemini-3.5-flash-lite")
        self.assertIn("gemini-3.5-flash-lite:generateContent", http.calls[1][1])
        self.assertEqual(http.calls[1][2]["x-goog-api-key"], "test-key")
        self.assertNotIn("key=", http.calls[1][1])                 # key never in the URL
        self.assertEqual(self.mem.month_usage()[0], 1)
        # the model choice is remembered: no second model listing
        http2 = FakeHttp([(200, gemini_reply({"answer": "B", "confidence": 0.9}))])
        ai.AIAnswerer(self.cfg, self.mem, http2).answer(Q)
        self.assertEqual(len(http2.calls), 1)

    def test_retired_model_is_replaced(self):
        self.mem.set_meta("gemini_model", "gemini-old")
        http = FakeHttp([(404, {}), (200, MODELS),
                         (200, gemini_reply({"answer": "B", "confidence": 0.9}))])
        r = ai.AIAnswerer(self.cfg, self.mem, http).answer(Q)
        self.assertEqual(r.answer.choice, "París")
        self.assertEqual(self.mem.get_meta("gemini_model"), "gemini-3.5-flash-lite")

    def test_rate_limit_pauses(self):
        self.mem.set_meta("gemini_model", "gemini-3.5-flash-lite")
        a = ai.AIAnswerer(self.cfg, self.mem, FakeHttp([(429, {"error": {"message": "quota"}})]))
        r = a.answer(Q)
        self.assertIsNone(r.answer)
        self.assertIn("busy", r.error)
        self.assertFalse(a.status()[0])

    def test_offline_and_bad_key(self):
        self.mem.set_meta("gemini_model", "gemini-3.5-flash-lite")
        r = ai.AIAnswerer(self.cfg, self.mem, FakeHttp([OSError("down")])).answer(Q)
        self.assertIn("no connection", r.error)
        r = ai.AIAnswerer(self.cfg, self.mem, FakeHttp([
            (400, {"error": {"message": "API key not valid"}})])).answer(Q)
        self.assertIn("not accepted", r.error)


class CostTests(Base):
    def test_paid_providers_need_permission_and_budget(self):
        self.cfg.ai_provider = "anthropic"
        secrets.set_key("ai:anthropic", "k")
        http = FakeHttp([])
        a = ai.AIAnswerer(self.cfg, self.mem, http)
        self.assertEqual(a.status(), (False, "this AI is paid; paid use is off (Settings)"))
        self.cfg.paid_api = True                       # limit is still $0
        self.assertEqual(a.status()[1], "monthly spending limit reached")
        self.cfg.monthly_limit = 1.0
        http.replies.append((200, {"content": [{"type": "text", "text": '{"answer": "B", '
                                                '"confidence": 0.9}'}],
                                   "usage": {"input_tokens": 400000, "output_tokens": 200000}}))
        r = a.answer(Q)
        self.assertEqual(r.answer.choice, "París")
        self.assertEqual(http.calls[0][3]["model"], "claude-haiku-5-5")
        self.assertAlmostEqual(self.mem.month_usage()[1], 1.4)     # 0.4 + 1.0 estimated
        self.assertEqual(a.status()[1], "monthly spending limit reached")
        self.assertEqual(len(http.calls), 1)                       # nothing sent after that

    def test_free_daily_cap_and_no_key(self):
        self.cfg.ai_daily_limit = 1
        self.mem.set_meta("gemini_model", "m")
        a = ai.AIAnswerer(self.cfg, self.mem, FakeHttp([(200, gemini_reply({"answer": "A"}))]))
        a.answer(Q)
        self.assertEqual(a.status()[1], "daily free-use limit reached")
        secrets.set_key("ai:gemini", None)
        self.assertEqual(a.status()[1], "no API key saved for the chosen AI")
        self.cfg.ai_provider = "none"
        self.assertFalse(a.status()[0])

    def test_search_only_when_enabled_and_unsure(self):
        self.mem.set_meta("gemini_model", "m")
        self.cfg.web_search, self.cfg.search_provider = True, "tavily"
        secrets.set_key("search:tavily", "t")
        http = FakeHttp([(200, gemini_reply({"answer": "A", "confidence": 0.3})),
                         (200, {"results": [{"title": "Francia", "content": "París es la capital"}]}),
                         (200, gemini_reply({"answer": "B", "confidence": 0.9}))])
        r = ai.AIAnswerer(self.cfg, self.mem, http).answer(Q)
        self.assertEqual((r.answer.choice, r.used_search, r.answer.source), ("París", True, "search"))
        self.assertIn("París es la capital", http.calls[2][3]["contents"][0]["parts"][0]["text"])
        # a confident first answer: no search
        http = FakeHttp([(200, gemini_reply({"answer": "B", "confidence": 0.9}))])
        ai.AIAnswerer(self.cfg, self.mem, http).answer(Q)
        self.assertEqual(len(http.calls), 1)


class EngineAITests(Base):
    """The engine asks the AI in the background and keeps watching the screen."""

    def test_thinking_then_ai_and_score(self):
        from puzzle_assistant import engine as E
        eng = E.Engine(self.cfg, queue.Queue())
        eng.memory = self.mem
        self.mem.set_meta("gemini_model", "m")
        eng.ai = ai.AIAnswerer(self.cfg, self.mem, FakeHttp(
            [(200, gemini_reply({"answer": "B", "explanation": "Es la capital.",
                                 "confidence": 0.9}))]))
        st = {"shown": None}
        ident = ("pregunta", "x")
        eng._ask_ai(Q, ident, {}, st)
        self.assertEqual(eng.out.get_nowait()["status"], "thinking")
        for _ in range(100):
            if st["ai_job"][1].done():
                break
            time.sleep(0.02)
        eng._poll_ai(st)
        m = eng.out.get_nowait()
        self.assertEqual((m["status"], m["answer"].choice), ("ai", "París"))
        self.assertEqual(st["check"][2], "ai")
        # the game then reveals: the AI's answer is scored, never stored as truth
        self.assertIsNone(self.mem.lookup(Q))
        self.mem.log_check(Q, st["check"][1], True, "ai")
        self.assertEqual(self.mem.stats()["ai_right"], 1)
        eng.stop()

    def test_low_confidence_is_unsure_and_failure_is_unknown(self):
        from puzzle_assistant import engine as E
        eng = E.Engine(self.cfg, queue.Queue())
        self.mem.set_meta("gemini_model", "m")
        eng.ai = ai.AIAnswerer(self.cfg, self.mem, FakeHttp(
            [(200, gemini_reply({"answer": "A", "confidence": 0.2})), OSError("down")]))
        for want in ("unsure", "unknown"):
            st = {"shown": None}
            eng._ask_ai(Q, ("p", want), {}, st)
            eng.out.get_nowait()
            while not st["ai_job"][1].done():
                time.sleep(0.02)
            eng._poll_ai(st)
            self.assertEqual(eng.out.get_nowait()["status"], want)
        eng.stop()

    def test_answer_arriving_too_late_is_dropped(self):
        from puzzle_assistant import engine as E
        eng = E.Engine(self.cfg, queue.Queue())
        self.mem.set_meta("gemini_model", "m")
        eng.ai = ai.AIAnswerer(self.cfg, self.mem, FakeHttp(
            [(200, gemini_reply({"answer": "B", "confidence": 0.9}))]))
        st = {"shown": None}
        eng._ask_ai(Q, ("p", "q"), {}, st)
        eng.out.get_nowait()
        st["shown"] = (("p", "q"), "game")         # player already answered
        while not st["ai_job"][1].done():
            time.sleep(0.02)
        eng._poll_ai(st)
        self.assertTrue(eng.out.empty())
        eng.stop()


class LearnedFactsTests(Base):
    """Stage 4.2: related answers the game confirmed are sent with the question."""

    def test_related_facts_go_into_the_request(self):
        from puzzle_assistant import storage
        storage.add_seed(self.mem)
        self.mem.set_meta("gemini_model", "m")
        q = Question("¿QUIÉN SOY?", "Trabajo en el casino y reparto las cartas",
                     ["Leo", "Mateo", "Rubén", "Tomás"])
        facts = self.mem.related(q)
        self.assertTrue(facts)
        self.assertIn("-> Leo", facts[0])           # shares the NPC names with the options
        self.assertLessEqual(len(facts), 5)
        http = FakeHttp([(200, gemini_reply({"answer": "A", "confidence": 0.8}))])
        r = ai.AIAnswerer(self.cfg, self.mem, http).answer(q)
        prompt = http.calls[0][3]["contents"][0]["parts"][0]["text"]
        self.assertIn("Facts confirmed by this game", prompt)
        self.assertEqual(r.facts, len(facts))
        self.assertEqual(len(http.calls), 1)                  # still one request

    def test_unrelated_or_unverified_are_not_sent(self):
        self.mem.save(Q, Answer(choice="París", source="ai"))            # not confirmed
        self.assertEqual(self.mem.related(Question("PREGUNTA", "¿Capital de Francia?",
                                                   ["Madrid", "París"])), [])
        self.mem.save(Q, Answer(choice="París", source="game", verified=True))
        self.assertEqual(self.mem.related(Q), [])                       # itself excluded
        self.assertEqual(self.mem.related(Question("PREGUNTA", "¿Cuánto pesa un elefante?",
                                                   ["1 t", "5 t"])), [])


class AtlasFactsTests(Base):
    def test_map_questions_get_world_atlas_facts(self):
        from puzzle_assistant.world_atlas.db import Atlas
        atlas = Atlas(os.path.join(tempfile.mkdtemp(), "atlas.db"))
        atlas.import_input()
        self.mem.set_meta("gemini_model", "m")
        http = FakeHttp([(200, gemini_reply({"answer": "A", "confidence": 0.8}))])
        ans = ai.AIAnswerer(self.cfg, self.mem, http)
        ans.atlas = atlas
        q = Question("PREGUNTA", "¿Qué lugar está al lado del Gran Casino?", ["Spa", "Mina", "Banco"])
        r = ans.answer(q)
        prompt = http.calls[0][3]["contents"][0]["parts"][0]["text"]
        self.assertIn("Gran Casino", prompt)
        self.assertIn("nearest on the map", prompt)
        self.assertGreaterEqual(r.facts, 1)
        atlas.close()


class ModelTests(unittest.TestCase):
    def test_answer_roundtrip_keeps_ai_source(self):
        a = Answer(choice="París", source="ai", confidence=0.8)
        self.assertEqual(Answer.from_dict(a.to_dict()).source, "ai")


if __name__ == "__main__":
    unittest.main()
