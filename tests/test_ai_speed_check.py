"""The standalone AI speed benchmark (benchmarks/ai_speed_check). No real network:
the HTTPS connection is replaced by a fake Gemini.

Checks: its copy of SK's request matches the program exactly; its question set
matches the game-confirmed starter answers; the key is never printed or written;
the key is sent only to Gemini's host; nothing is written outside the report
folder; rate limits and the 'thinking off' probe behave."""
import base64
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "benchmarks", "ai_speed_check"))

import ai_speed_check as bench  # noqa: E402
from puzzle_assistant.qa import ai  # noqa: E402
from puzzle_assistant.qa.model import Question  # noqa: E402

KEY = "AIzaFAKE-test-key-1234567890abcdef"
MODELS = {"models": [
    {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"],
     "thinking": True},
    {"name": "models/gemini-3.6-flash-lite-preview", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.0-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]}]}


def to_question(q):
    return Question(q.category, q.text, list(q.options), kind=q.kind, clues=list(q.clues),
                    right=list(q.right), language=q.language)


def right_reply(q):
    """The reply a perfect model would give."""
    if q.kind == bench.CHOICE:
        return {"answer": bench.LETTERS[q.options.index(q.answer)], "explanation": "x", "confidence": 0.9}
    if q.kind == bench.ORDER:
        return {"order": [bench.LETTERS[q.options.index(o)] for o in q.answer], "explanation": "x",
                "confidence": 0.9}
    return {"pairs": {bench.LETTERS[q.options.index(k)]: str(q.right.index(v) + 1)
                      for k, v in q.answer.items()}, "explanation": "x", "confidence": 0.9}


class FakeResp:
    will_close = False

    def __init__(self, status, body: bytes, headers=None):
        self.status, self._buf, self._h = status, io.BytesIO(body), headers or {}

    def read(self, n=-1):
        return self._buf.read() if n is None or n < 0 else self._buf.read(n)

    def readline(self):
        return self._buf.readline()

    def getheader(self, name, default=None):
        return self._h.get(name, default)


class FakeGemini:
    """Stands in for generativelanguage.googleapis.com."""

    def __init__(self, wrong=(), rate_once=False, refuse_budget=False, text_for=(), slow=None):
        self.log = []                 # (host, method, path, headers, body)
        self.connections = 0
        self.wrong, self.rate_once, self.refuse_budget = set(wrong), rate_once, refuse_budget
        self.text_for = set(text_for)  # reply with the option TEXT (like the real model on q6)
        self.slow = dict(slow or {})   # question index -> seconds (interrupted by a cancel)
        self.rated = False
        self.aborted = 0

    def connection(self, host, port, context=None, timeout=None):
        fake = self

        import threading as _th

        class Sock:
            def __init__(self, conn):
                self.conn = conn

            def settimeout(self, t):
                pass

            def shutdown(self, how):
                self.conn.shut.set()

        class Conn:
            def __init__(self):
                self.host = host
                self.sock = None
                self.shut = _th.Event()

            def set_tunnel(self, h, p):
                self.host = h

            def connect(self):
                fake.connections += 1
                self.sock = Sock(self)

            def request(self, method, path, body=None, headers=None):
                self.last = (method, path, json.loads(body) if body else None)
                fake.log.append((self.host, method, path, dict(headers or {}), self.last[2]))

            def getresponse(self):
                wait = fake.delay_for(*self.last)
                if wait and self.shut.wait(wait):
                    fake.aborted += 1
                    raise ConnectionAbortedError("connection shut down")
                return fake.respond(*self.last)

            def close(self):
                self.sock = None
        return Conn()

    def question_of(self, body):
        prompt = body["contents"][0]["parts"][0]["text"]
        return next(q for q in bench.QUESTIONS if f"Question: {q.text}" in prompt
                    and all(o in prompt for o in q.options))

    def delay_for(self, method, path, body):
        if method != "POST" or not self.slow:
            return 0.0
        return self.slow.get(bench.QUESTIONS.index(self.question_of(body)), 0.0)

    def respond(self, method, path, body):
        if method == "GET":
            return FakeResp(200, json.dumps(MODELS).encode())
        if self.rate_once and not self.rated:
            self.rated = True
            return FakeResp(429, b'{"error": {"message": "quota"}}')
        thinking = body["generationConfig"].get("thinkingConfig")
        if thinking and self.refuse_budget and "thinkingBudget" in thinking:
            return FakeResp(400, b'{"error": {"message": "thinkingBudget not supported"}}')
        prompt = body["contents"][0]["parts"][0]["text"]
        q = next(q for q in bench.QUESTIONS if f"Question: {q.text}" in prompt
                 and all(o in prompt for o in q.options))
        reply = right_reply(q)
        if bench.QUESTIONS.index(q) in self.text_for and q.kind == bench.CHOICE:
            reply["answer"] = q.answer
        if bench.QUESTIONS.index(q) in self.wrong and q.kind == bench.CHOICE:
            reply["answer"] = next(l for l in "ABCD" if q.options[bench.LETTERS.index(l)] != q.answer)
        usage = {"promptTokenCount": 120, "candidatesTokenCount": 25,
                 "thoughtsTokenCount": 0 if thinking else 40}
        if "streamGenerateContent" in path:
            text = json.dumps(reply)
            chunks = [text[:12], text[12:]]
            sse = b"".join(b"data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": c}]}}],
                                                   "usageMetadata": usage}).encode() + b"\r\n\r\n"
                           for c in chunks)
            return FakeResp(200, sse)
        out = {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True},
                                                     {"text": json.dumps(reply)}]}}],
               "usageMetadata": usage}
        return FakeResp(200, json.dumps(out).encode())


def write_keys(path, key=KEY):
    raw = json.dumps({"ai:gemini": key, "search:tavily": "other-secret"}).encode()
    with open(path, "wb") as fh:
        fh.write(b"PLAIN:" + base64.b64encode(raw))


def tree(path):
    out = {}
    for root, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, path)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class CopyMatchesProgram(unittest.TestCase):
    def test_system_prompt_is_identical(self):
        self.assertEqual(bench.SYSTEM, ai.SYSTEM)

    def test_prompts_are_identical_for_every_question(self):
        for q in bench.QUESTIONS:
            self.assertEqual(bench.build_prompt(q), ai.build_prompt(to_question(q)), q.text)
            self.assertEqual(bench.build_prompt(q, ["s"], ["f"]),
                             ai.build_prompt(to_question(q), ["s"], ["f"]), q.text)

    def test_request_body_matches_gemini_ask(self):
        sent = {}

        def http(method, url, headers, body, timeout):
            sent["body"] = body
            return 200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        cfg = type("C", (), {"ai_model": "m"})()
        p = ai.Gemini(cfg, None, http)
        with mock.patch.object(ai.secrets, "get_key", return_value="k"):
            for q in bench.QUESTIONS:
                user = ai.build_prompt(to_question(q))
                p.ask(ai.SYSTEM, user, 1.0)
                self.assertEqual(sent["body"], bench.request_body(q))

    def test_reply_parsing_matches_program(self):
        for q in bench.QUESTIONS:
            text = json.dumps(right_reply(q))
            got, conf = bench.parse_reply(text, q)
            ans = ai.parse_reply(text, to_question(q))
            want = {"choice": ans.choice, "order": ans.order, "pairs": ans.pairs}[q.kind]
            self.assertEqual(got, want)
            self.assertEqual(got, q.answer)
            self.assertEqual(conf, ans.confidence)

    def test_model_choice_matches_program(self):
        names = bench.usable_models(MODELS["models"])
        cfg = type("C", (), {"ai_model": ""})()
        p = ai.Gemini(cfg, None, lambda *a: (200, MODELS))
        with mock.patch.object(ai.secrets, "get_key", return_value="k"):
            self.assertEqual(bench.sk_pick(names), p._pick_model())
        self.assertEqual(bench.flash_lite_models(names),
                         ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.0-flash-lite"])


class QuestionSet(unittest.TestCase):
    def test_twenty_questions_with_valid_answers(self):
        self.assertEqual(len(bench.QUESTIONS), 20)
        for q in bench.QUESTIONS:
            got, _ = bench.parse_reply(json.dumps(right_reply(q)), q)
            self.assertEqual(got, q.answer)

    def test_starter_questions_use_the_game_revealed_answers(self):
        with open(os.path.join(ROOT, "assets", "seed_questions.json"), encoding="utf-8") as fh:
            seeds = json.load(fh)["questions"]
        for s in seeds:
            q = next(q for q in bench.QUESTIONS if q.options == s["options"])
            self.assertEqual(q.text, s["text"])
            a = s["answer"]
            self.assertEqual(q.answer, a.get("choice") or a.get("order") or a.get("pairs"))


class RunSafely(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "SK Assistant")       # stands in for %APPDATA%
        self.out = os.path.join(self.tmp.name, "results")
        os.makedirs(self.data)
        self.keys = os.path.join(self.data, "keys.bin")
        write_keys(self.keys)
        with open(os.path.join(self.data, "config.json"), "w") as fh:
            json.dump({"ai_model": "", "ai_provider": "gemini"}, fh)
        for db in ("habbosk.db", "atlas.db", "knowledge.db"):
            with open(os.path.join(self.data, db), "wb") as fh:
                fh.write(b"real data")

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        name = [f for f in os.listdir(self.out) if f.endswith(".json")][0]
        with open(os.path.join(self.out, name), encoding="utf-8") as fh:
            return json.load(fh)

    def run_main(self, fake, *extra):
        args = ["--yes", "--pace", "0", "--rate-wait", "0", "--out", self.out,
                "--keys-file", self.keys, "--config-file", os.path.join(self.data, "config.json"), *extra]
        buf = io.StringIO()
        real_open = open
        opened = []

        def spy_open(file, mode="r", *a, **k):
            opened.append((os.path.abspath(str(file)), mode))
            return real_open(file, mode, *a, **k)
        with mock.patch.object(bench.http.client, "HTTPSConnection", fake.connection), \
                mock.patch.dict(os.environ, {"HTTPS_PROXY": "", "https_proxy": ""}), \
                mock.patch("builtins.open", spy_open), contextlib.redirect_stdout(buf):
            code = bench.main(args)
        return code, buf.getvalue(), opened

    def test_full_run_never_reveals_the_key_and_writes_only_the_report(self):
        before = tree(self.data)
        fake = FakeGemini(wrong={13})
        code, out, opened = self.run_main(fake)
        self.assertEqual(code, 0)
        self.assertEqual(tree(self.data), before)                   # SK's folder untouched
        self.assertNotIn(KEY, out)
        self.assertNotIn("other-secret", out)
        reports = os.listdir(self.out)
        self.assertEqual(len(reports), 2)
        for f in reports:
            with open(os.path.join(self.out, f), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(KEY, text)
            self.assertNotIn(KEY[:12], text)
            self.assertNotIn("other-secret", text)
        # files: only keys.bin and config.json read; writes only into the report folder
        for path, mode in opened:
            if any(c in mode for c in "wax+"):
                self.assertTrue(path.startswith(os.path.abspath(self.out)), path)
            else:
                self.assertIn(os.path.basename(path), ("keys.bin", "config.json"), path)
        self.assertFalse(any(os.path.basename(p).endswith(".db") for p, _ in opened))
        # the key went only to Gemini's host, only in the header
        for host, _m, path, headers, body in fake.log:
            self.assertEqual(host, bench.HOST)
            self.assertEqual(headers.get("x-goog-api-key"), KEY)
            self.assertNotIn(KEY, path)
            self.assertNotIn(KEY, json.dumps(body or {}))
        rep = self.report()
        titles = [r["title"] for r in rep["results"]]
        self.assertEqual(titles[0], "SK today (new connection each time)")
        base = rep["results"][0]["summary"]
        self.assertEqual((base["requests"], base["correct"], base["errors"]), (20, 19, 0))
        self.assertEqual(rep["model_tested_as_current"], "gemini-3.5-flash-lite")
        self.assertIn("alternative gemini-3.1-flash-lite + reused connection", titles)
        self.assertIn("alternative gemini-3.0-flash-lite + reused connection", titles)
        stream = next(r for r in rep["results"] if r["streamed"])
        self.assertEqual(stream["summary"]["requests"], 10)
        self.assertGreater(stream["summary"]["stream_letter_median_ms"], 0)
        think = next(r for r in rep["results"] if r["title"] == "SK today + thinking off")
        self.assertEqual(think["thinking"], {"thinkingBudget": 0})
        self.assertEqual(think["summary"]["thinking_tokens_avg"], 0)
        self.assertEqual(base["thinking_tokens_avg"], 40)

    def test_new_connection_each_time_vs_reused(self):
        fake = FakeGemini()
        self.run_main(fake, "--quick")
        self.assertEqual(fake.connections, 1 + 20)          # model list + one per request
        fake2 = FakeGemini()
        with mock.patch.object(bench.http.client, "HTTPSConnection", fake2.connection):
            c = bench.Gemini(KEY, fresh=False)
            for q in bench.QUESTIONS[:5]:
                c.call("POST", "/models/m:generateContent", bench.request_body(q))
        self.assertEqual(fake2.connections, 1)

    def test_quick_run_sends_about_twenty_requests(self):
        fake = FakeGemini()
        code, out, _ = self.run_main(fake, "--quick")
        self.assertEqual(code, 0)
        self.assertEqual(sum(1 for e in fake.log if e[1] == "POST"), 20)
        self.assertEqual(sum(1 for e in fake.log if e[1] == "GET"), 1)

    def test_rate_limit_waits_and_retries_once(self):
        fake = FakeGemini(rate_once=True)
        code, out, _ = self.run_main(fake, "--quick")
        rep = self.report()
        s = rep["results"][0]["summary"]
        self.assertEqual((s["rate_limited"], s["errors"], s["correct"]), (1, 0, 20))

    def test_thinking_probe_falls_back_to_thinking_level(self):
        fake = FakeGemini(refuse_budget=True)
        self.run_main(fake)
        rep = self.report()
        think = next(r for r in rep["results"] if r["title"] == "SK today + thinking off")
        self.assertEqual(think["thinking"], {"thinkingLevel": "minimal"})

    def test_no_key_sends_nothing(self):
        write_keys(self.keys, key="")
        fake = FakeGemini()
        code, out, _ = self.run_main(fake)
        self.assertEqual(code, 2)
        self.assertEqual(fake.log, [])

    def run_compare(self, fake):
        """--compare imports SK's code and swaps its key functions: restore them after."""
        from puzzle_assistant import secrets as sk_secrets
        with mock.patch.object(sk_secrets, "get_key", sk_secrets.get_key), \
                mock.patch.object(sk_secrets, "set_key", sk_secrets.set_key), \
                mock.patch.object(sk_secrets, "has_key", sk_secrets.has_key), \
                mock.patch.dict(os.environ, {"SK_SPEEDCHECK_TEST": "1"}):
            saved = os.environ.get("SK_ASSISTANT_DATA")
            try:
                return self.run_main(fake, "--compare")
            finally:
                if saved is None:
                    os.environ.pop("SK_ASSISTANT_DATA", None)
                else:
                    os.environ["SK_ASSISTANT_DATA"] = saved

    def test_compare_before_after(self):
        before = tree(self.data)
        slow = {i: 0.6 for i, _ in bench.STALE_PAIRS}            # the first question of each pair
        fake = FakeGemini(text_for={5}, slow=slow)
        code, out, opened = self.run_compare(fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(tree(self.data), before)               # SK's real folder untouched
        self.assertNotIn(KEY, out)
        for path, mode in opened:
            if any(c in mode for c in "wax+"):
                self.assertTrue(path.startswith(os.path.abspath(self.out)), path)
        self.assertFalse(any(os.path.basename(p).endswith(".db") for p, _ in opened))
        for root, _d, files in os.walk(self.out):
            for f in files:
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    self.assertNotIn(KEY, fh.read(), f)
        rep = self.report()
        titles = [r["title"] for r in rep["results"]]
        self.assertEqual(len(titles), 4)
        before_rows, after_rows = rep["results"][0]["rows"], rep["results"][1]["rows"]
        self.assertEqual(len(before_rows), 20)
        self.assertEqual(len(after_rows), 20)
        q6_before = next(r for r in before_rows if r["q"] == 6)
        q6_after = next(r for r in after_rows if r["q"] == 6)
        self.assertIn("not an option", q6_before["error"])      # v3.3 throws the right answer away
        self.assertTrue(q6_after["correct"])                     # D keeps it
        self.assertEqual(rep["paired_accuracy"]["letter_question_after"], True)
        self.assertEqual(rep["results"][1]["summary"]["correct"], 20)
        self.assertEqual(rep["results"][0]["summary"]["correct"], 19)
        # B: in the "question changed" cases the old request is stopped, the new
        # question doesn't wait for it
        self.assertEqual(rep["stale_requests_stopped"], 5)
        sb = rep["results"][2]["summary"]["total_median_ms"]
        sa = rep["results"][3]["summary"]["total_median_ms"]
        self.assertGreater(sb, 200)                              # waited behind the slow one
        self.assertLess(sa, sb)
        # A: the log in the sandbox has only allowed fields
        from puzzle_assistant import diagnostics
        self.assertLessEqual(set(rep["diagnostics_log_fields"]), set(diagnostics.FIELDS) | {"ts"})
        sandbox = [d for d in os.listdir(self.out) if d.startswith("sandbox-")]
        self.assertEqual(len(sandbox), 1)
        self.assertEqual(os.listdir(os.path.join(self.out, sandbox[0])), ["diagnostics"])
        # C: the development copy reused its connection
        self.assertLess(fake.connections, 20 + 20 + 20)

    def test_redactor(self):
        hide = bench.Redactor(KEY)
        self.assertEqual(hide(f"error for {KEY}!"), "error for [key hidden]!")


if __name__ == "__main__":
    unittest.main()
