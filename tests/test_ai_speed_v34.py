"""v3.4 AI speed changes A-D (owner-approved). No real network: fake connections
and a local test server on 127.0.0.1.

A  timing diagnostics: only allowed fields, no text, size cap
B  safe cancellation: the newest question wins, strict limits, stale answers ignored
C  connection reuse: kept-open Gemini connection, reconnect once, proxy -> v3.3 method
D  letter matching: an option's exact text accepted only when unambiguous
"""
import http.client
import http.server
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from puzzle_assistant import diagnostics, secrets  # noqa: E402
from puzzle_assistant.config import Config  # noqa: E402
from puzzle_assistant.qa import ai, ai_jobs, transport  # noqa: E402
from puzzle_assistant.qa.memory import Memory  # noqa: E402
from puzzle_assistant.qa.model import ORDER, Question  # noqa: E402

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
KEY = "AIzaSECRET-test-key-0123456789"


def gemini_reply(obj):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 20}}


# ---------------------------------------------------------------------------
# D - letter matching
# ---------------------------------------------------------------------------
class LetterMatching(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(secrets, "get_key", return_value=KEY)
        p.start()
        self.addCleanup(p.stop)

    def pick(self, raw, options):
        return options[ai.option_index(raw, options)]

    def test_option_text_instead_of_letter(self):
        letters = ["U", "X", "W", "V"]                 # the baseline's "¿Qué letra sigue?"
        self.assertEqual(self.pick("V", letters), "V")
        self.assertEqual(self.pick(" V ", letters), "V")
        self.assertEqual(self.pick("D", letters), "V")  # the letter still works
        self.assertEqual(self.pick("70", ["79", "69", "70", "71"]), "70")

    def test_letter_forms_as_before(self):
        opts = ["Madrid", "París", "Roma"]
        for raw in ("B", "b", "B.", "(B)", "B)", "B) París", "B. París", "B París", "B: París"):
            self.assertEqual(self.pick(raw, opts), "París", raw)

    def test_ambiguous_replies_are_rejected(self):
        with self.assertRaises(ai.AmbiguousReply):        # letter B = "A", text "B" = option C
            ai.option_index("B", ["C", "A", "B", "D"])
        with self.assertRaises(ai.AmbiguousReply):
            ai.option_index("A", ["C", "A", "B", "D"])
        with self.assertRaises(ai.AmbiguousReply):        # letter and text name different options
            ai.option_index("B) Roma", ["Madrid", "París", "Roma"])
        with self.assertRaises(ai.AmbiguousReply):        # duplicate option texts
            ai.option_index("Sí", ["Sí", "No", "Sí"])
        # letter and text agree: accepted
        self.assertEqual(ai.option_index("D", ["C", "A", "B", "D"]), 3)

    def test_first_character_is_no_longer_taken_as_the_letter(self):
        # v3.3: "Bambú"[0] == "B" silently meant option B
        opts = ["Pasto", "Néctar", "Eucalipto", "Bambú"]
        self.assertEqual(self.pick("Bambú", opts), "Bambú")
        with self.assertRaises(ValueError):
            ai.option_index("Bambúes", opts)
        with self.assertRaises(ValueError):
            ai.option_index("E", ["a", "b", "c", "d"])     # not an option's letter
        with self.assertRaises(ValueError):
            ai.option_index("", ["a", "b"])

    def test_unicode_forms_match(self):
        decomposed = "París"
        self.assertEqual(self.pick(decomposed, ["Madrid", "París"]), "París")

    def test_parse_reply_choice_and_order(self):
        q = Question("PREGUNTA", "¿Qué letra sigue?", ["U", "X", "W", "V"], clues=["F, J, N, R, ?"])
        a = ai.parse_reply(json.dumps({"answer": "V", "confidence": 0.9}), q)
        self.assertEqual(a.choice, "V")
        o = Question("ORDENAR", "Ordena", ["Pulpo", "Sardina", "Atún"], kind=ORDER)
        a = ai.parse_reply(json.dumps({"order": ["Sardina", "A", "Atún"], "confidence": 0.8}), o)
        self.assertEqual(a.order, ["Sardina", "Pulpo", "Atún"])
        with self.assertRaises(ValueError):
            ai.parse_reply(json.dumps({"order": ["B", "Sardina", "C"]}), o)   # not a permutation
        with self.assertRaises(ValueError):
            ai.parse_reply(json.dumps({"order": "ABC"}), o)

    def test_ambiguous_reply_is_reported_as_ambiguous(self):
        q = Question("PREGUNTA", "x", ["C", "A", "B", "D"])
        res = ai_answerer(FakeHttp([(200, gemini_reply({"answer": "B", "confidence": 1}))])).answer(q)
        self.assertIsNone(res.answer)
        self.assertEqual(res.error_kind, "ambiguous")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class FakeHttp:
    def __init__(self, replies, delay=0.0):
        self.replies, self.delay, self.calls = list(replies), delay, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if self.delay:
            time.sleep(self.delay)
        r = self.replies.pop(0) if self.replies else (200, gemini_reply({"answer": "A", "confidence": 1}))
        if isinstance(r, BaseException):
            raise r
        return r


_TMP = tempfile.mkdtemp()


def ai_answerer(http, mem=None):
    cfg = Config()
    cfg.ai_provider, cfg.ai_model = "gemini", "m"
    a = ai.AIAnswerer(cfg, mem, http)
    return a


class KeyPatch(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(secrets, "get_key", return_value=KEY)
        p.start()
        self.addCleanup(p.stop)


# ---------------------------------------------------------------------------
# C - connection reuse (fake connections)
# ---------------------------------------------------------------------------
class FakeSock:
    def __init__(self, conn):
        self.conn = conn

    def settimeout(self, t):
        self.conn.timeout = t

    def shutdown(self, how):
        self.conn.shut.set()


class FakeResp:
    def __init__(self, status, payload, will_close=False):
        self.status, self._raw, self.will_close = status, json.dumps(payload).encode(), will_close

    def read(self):
        return self._raw


class FakeConn:
    """script: list of actions for successive requests on THIS connection:
    a (status, payload[, will_close]) tuple, an exception, or "hang"."""

    def __init__(self, server, host, timeout):
        self.server, self.host, self.timeout = server, host, timeout
        self.sock = None
        self.shut = threading.Event()
        self.closed = False

    def connect(self):
        self.server.connects += 1
        self.sock = FakeSock(self)

    def request(self, method, path, body=None, headers=None):
        self.server.sent.append((self.host, method, path, dict(headers or {})))

    def getresponse(self):
        act = self.server.next_action(self)
        if act == "hang":
            if self.shut.wait(5):
                raise ConnectionAbortedError("socket shut down")
            raise socket.timeout("timed out")
        if isinstance(act, BaseException):
            raise act
        return FakeResp(*act)

    def close(self):
        self.closed = True
        self.sock = None


class FakeServer:
    def __init__(self, actions):
        self.actions = list(actions)
        self.connects = 0
        self.sent = []
        self.conns = []

    def factory(self, host, timeout):
        c = FakeConn(self, host, timeout)
        self.conns.append(c)
        return c

    def next_action(self, conn):
        return self.actions.pop(0) if self.actions else (200, {"ok": True})


class ConnectionReuse(unittest.TestCase):
    def make(self, actions, proxies=None, clock=None):
        srv = FakeServer(actions)
        fallback = FakeHttp([(200, {"via": "urllib"})] * 10)
        kw = {"clock": clock} if clock else {}
        h = transport.KeepAliveHttp(fallback=fallback, proxies=lambda: proxies or {},
                                    connection_factory=srv.factory, **kw)
        return h, srv, fallback

    def test_second_request_reuses_the_connection(self):
        h, srv, _ = self.make([])
        self.assertEqual(h("POST", GEMINI, {"x-goog-api-key": KEY}, {"a": 1}, 8.0)[0], 200)
        self.assertFalse(transport.last_call()["reused"])
        h("POST", GEMINI, {"x-goog-api-key": KEY}, {"a": 1}, 8.0)
        info = transport.last_call()
        self.assertTrue(info["reused"])
        self.assertEqual(info["connect_ms"], 0.0)
        self.assertEqual(srv.connects, 1)
        self.assertEqual(len(srv.sent), 2)
        self.assertEqual(srv.sent[0][2], "/v1beta/models/m:generateContent")

    def test_dropped_connection_is_reopened_once(self):
        h, srv, _ = self.make([(200, {}), http.client.RemoteDisconnected("closed"), (200, {"n": 2})])
        h("POST", GEMINI, {}, {}, 8.0)
        status, data = h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual((status, data), (200, {"n": 2}))
        self.assertTrue(transport.last_call()["reconnected"])
        self.assertEqual(srv.connects, 2)
        self.assertEqual(len(srv.sent), 3)                  # 1 + the failed one + the resend

    def test_reconnect_happens_only_once(self):
        h, srv, _ = self.make([(200, {}), ConnectionResetError(), ConnectionResetError()])
        h("POST", GEMINI, {}, {}, 8.0)
        with self.assertRaises(ConnectionResetError):
            h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(len(srv.sent), 3)

    def test_failure_on_a_fresh_connection_is_not_retried(self):
        h, srv, _ = self.make([ConnectionResetError()])
        with self.assertRaises(ConnectionResetError):
            h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(len(srv.sent), 1)

    def test_hang_times_out_and_is_not_retried(self):
        h, srv, _ = self.make([(200, {})])
        h("POST", GEMINI, {}, {}, 8.0)
        with mock.patch.object(FakeConn, "getresponse", side_effect=socket.timeout("timed out")):
            with self.assertRaises(TimeoutError):
                h("POST", GEMINI, {}, {}, 0.2)
        self.assertEqual(len(srv.sent), 2)
        h("POST", GEMINI, {}, {}, 8.0)                      # a new connection afterwards
        self.assertEqual(srv.connects, 2)

    def test_overload_status_is_returned_and_connection_kept(self):
        h, srv, _ = self.make([(503, {"error": {"message": "high demand"}}), (200, {})])
        self.assertEqual(h("POST", GEMINI, {}, {}, 8.0)[0], 503)
        h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(srv.connects, 1)

    def test_server_closing_after_the_reply_means_a_new_connection(self):
        h, srv, _ = self.make([(200, {}, True), (200, {})])
        h("POST", GEMINI, {}, {}, 8.0)
        h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(srv.connects, 2)

    def test_long_idle_connection_is_not_reused(self):
        now = [100.0]
        h, srv, _ = self.make([], clock=lambda: now[0])
        h("POST", GEMINI, {}, {}, 8.0)
        now[0] += 51.0                                    # the documented limit is 50 s
        h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(srv.connects, 2)
        now[0] += 30.0                                    # within the limit: reused
        h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(srv.connects, 2)

    def test_proxy_keeps_the_v33_method(self):
        h, srv, fb = self.make([], proxies={"https": "http://proxy:8080"})
        self.assertEqual(h("POST", GEMINI, {}, {}, 8.0), (200, {"via": "urllib"}))
        self.assertEqual(srv.connects, 0)
        self.assertEqual(len(fb.calls), 1)
        h2, srv2, _ = self.make([], proxies={"all": "socks5://p"})
        h2("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual(srv2.connects, 0)

    def test_proxy_check_failing_keeps_the_v33_method(self):
        srv = FakeServer([])
        fb = FakeHttp([(200, {})])

        def broken():
            raise OSError("registry")
        h = transport.KeepAliveHttp(fallback=fb, proxies=broken, connection_factory=srv.factory)
        h("POST", GEMINI, {}, {}, 8.0)
        self.assertEqual((srv.connects, len(fb.calls)), (0, 1))

    def test_other_hosts_keep_the_v33_method(self):
        h, srv, fb = self.make([])
        for url in ("https://api.anthropic.com/v1/messages", "https://api.tavily.com/search",
                    "http://generativelanguage.googleapis.com/v1beta/x"):
            h("POST", url, {}, {}, 8.0)
        self.assertEqual(srv.connects, 0)
        self.assertEqual(len(fb.calls), 3)

    def test_each_lane_has_its_own_connection(self):
        h, srv, _ = self.make([])
        t = threading.Thread(target=lambda: h("POST", GEMINI, {}, {}, 8.0))
        h("POST", GEMINI, {}, {}, 8.0)
        t.start()
        t.join()
        self.assertEqual(srv.connects, 2)

    def test_cancel_stops_a_hanging_request_at_once(self):
        h, srv, _ = self.make(["hang"])
        tok = ai_jobs.CancelToken()
        out = {}

        def run():
            ai_jobs._local.token = tok
            try:
                h("POST", GEMINI, {}, {}, 8.0)
            except transport.Aborted as exc:
                out["aborted"], out["sent"] = True, exc.sent
            finally:
                ai_jobs._local.token = None
        t = threading.Thread(target=run)
        t0 = time.monotonic()
        t.start()
        time.sleep(0.1)
        tok.cancel()
        t.join(2)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(out, {"aborted": True, "sent": True})

    def test_cancelled_before_sending_sends_nothing(self):
        h, srv, _ = self.make([])
        tok = ai_jobs.CancelToken()
        tok.cancel()
        ai_jobs._local.token = tok
        try:
            with self.assertRaises(transport.Aborted) as cm:
                h("POST", GEMINI, {}, {}, 8.0)
        finally:
            ai_jobs._local.token = None
        self.assertFalse(cm.exception.sent)
        self.assertEqual(srv.sent, [])


# ---------------------------------------------------------------------------
# C - against a real local HTTP server (real sockets, real keep-alive)
# ---------------------------------------------------------------------------
class LocalGemini(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"                 # ok | close_after | overload | hang
    conns = set()
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        with self.lock:
            LocalGemini.conns.add(self.client_address)
        mode = LocalGemini.mode
        if mode == "hang":
            time.sleep(3)
        status = 503 if mode == "overload" else 200
        body = json.dumps({"error": {"message": "high demand"}} if status == 503 else
                          gemini_reply({"answer": "B", "confidence": 0.9})).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if mode == "close_after":                     # server silently drops the kept connection
            self.close_connection = True


class QuietServer(http.server.ThreadingHTTPServer):
    """The test server: a reply the client has stopped waiting for is expected
    (cancellation tests), so don't print that as an error."""

    def handle_error(self, request, client_address):
        pass


class RealSockets(KeyPatch):
    @classmethod
    def setUpClass(cls):
        cls.srv = QuietServer(("127.0.0.1", 0), LocalGemini)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        super().setUp()
        LocalGemini.mode = "ok"
        LocalGemini.conns = set()
        port = self.port
        self.http = transport.KeepAliveHttp(
            proxies=lambda: {},
            connection_factory=lambda host, t: http.client.HTTPConnection("127.0.0.1", port, timeout=t))
        self.addCleanup(self.http.close)

    def test_answers_over_one_kept_connection(self):
        a = ai_answerer(self.http)
        q = Question("PREGUNTA", "¿Capital de Francia?", ["Madrid", "París", "Roma"])
        r1, r2, r3 = a.answer(q), a.answer(q), a.answer(q)
        self.assertEqual([r.answer.choice for r in (r1, r2, r3)], ["París"] * 3)
        self.assertEqual(len(LocalGemini.conns), 1)
        self.assertFalse(r1.reused)
        self.assertTrue(r2.reused and r3.reused)

    def test_server_closing_the_connection_is_handled(self):
        a = ai_answerer(self.http)
        q = Question("PREGUNTA", "x", ["A1", "B1"])
        LocalGemini.mode = "close_after"
        self.assertEqual(a.answer(q).answer.choice, "B1")
        time.sleep(0.05)
        LocalGemini.mode = "ok"
        r = a.answer(q)
        self.assertEqual(r.answer.choice, "B1")
        self.assertEqual(r.error, "")

    def test_overload_is_reported_as_overload(self):
        LocalGemini.mode = "overload"
        r = ai_answerer(self.http).answer(Question("PREGUNTA", "x", ["A1", "B1"]))
        self.assertIsNone(r.answer)
        self.assertEqual(r.error_kind, "overload")
        self.assertIn("503", r.error)

    def test_hang_is_a_timeout(self):
        LocalGemini.mode = "hang"
        t0 = time.monotonic()
        r = ai_answerer(self.http).answer(Question("PREGUNTA", "x", ["A1", "B1"]), timeout=0.4)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(r.error_kind, "timeout")

    def test_cancel_stops_a_real_hanging_request_when_nothing_wakes_the_socket(self):
        """Like Windows (owner's PC): neither shutdown nor close ends a waiting
        read. The request must still stop quickly: it waits in 0.1 s steps."""
        LocalGemini.mode = "hang"
        sched = ai_jobs.AiScheduler()
        a = ai_answerer(self.http)
        with mock.patch.object(ai_jobs, "_shutdown", lambda conn: None):
            job = sched.submit("q1", a.answer, Question("PREGUNTA", "x", ["A1", "B1"]), 8.0)
            time.sleep(0.3)
            t0 = time.monotonic()
            sched.cancel(job)
            res = job.future.result(timeout=2)
            self.assertLess(time.monotonic() - t0, 0.5)
        self.assertTrue(res.stale)
        self.assertEqual(res.error_kind, "stale")
        sched.shutdown()

    def test_waiting_in_steps_keeps_the_time_limit(self):
        LocalGemini.mode = "hang"
        sched = ai_jobs.AiScheduler()
        a = ai_answerer(self.http)
        with mock.patch.object(ai_jobs, "_shutdown", lambda conn: None):
            t0 = time.monotonic()
            job = sched.submit("q1", a.answer, Question("PREGUNTA", "x", ["A1", "B1"]), 0.5)
            res = job.future.result(timeout=3)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual(res.error_kind, "timeout")
        sched.shutdown()

    def test_cancel_stops_a_real_hanging_request(self):
        LocalGemini.mode = "hang"
        sched = ai_jobs.AiScheduler()
        a = ai_answerer(self.http)
        job = sched.submit("q1", a.answer, Question("PREGUNTA", "x", ["A1", "B1"]), 8.0)
        time.sleep(0.2)
        t0 = time.monotonic()
        sched.cancel(job)
        res = job.future.result(timeout=2)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertTrue(res.stale)
        self.assertEqual(res.error_kind, "stale")
        sched.shutdown()


# ---------------------------------------------------------------------------
# B - the scheduler
# ---------------------------------------------------------------------------
class Blocker:
    """A request that runs until released (it ignores cancellation, like a
    request through a proxy that can't be stopped)."""

    def __init__(self):
        self.release = threading.Event()
        self.started = []
        self.lock = threading.Lock()

    def __call__(self, name):
        with self.lock:
            self.started.append(name)
        self.release.wait(5)
        return name


class Scheduler(unittest.TestCase):
    def test_newest_question_does_not_wait_for_an_old_one(self):
        b = Blocker()
        s = ai_jobs.AiScheduler()
        old = s.submit("q1", b, "q1")
        time.sleep(0.05)
        new = s.submit("q2", lambda: "fast")
        self.assertEqual(new.future.result(timeout=1), "fast")
        self.assertTrue(old.token.cancelled)
        b.release.set()
        s.shutdown()

    def test_strict_limits_two_running_one_waiting(self):
        b = Blocker()
        stale = []
        s = ai_jobs.AiScheduler(on_stale=lambda job, res: stale.append(job.ident))
        jobs = [s.submit(f"q{i}", b, f"q{i}") for i in range(6)]
        time.sleep(0.2)
        self.assertEqual(sorted(b.started), ["q0", "q1"])          # 2 lanes only
        self.assertLessEqual(s.active(), 3)                        # 2 running + newest waiting
        for j in jobs[2:5]:                                        # older waiting ones: never sent
            self.assertTrue(j.future.cancelled())
        self.assertFalse(jobs[5].future.cancelled())
        b.release.set()
        self.assertEqual(jobs[5].future.result(timeout=2), "q5")
        time.sleep(0.1)
        self.assertEqual(sorted(b.started), ["q0", "q1", "q5"])    # q2-q4 never ran
        self.assertEqual(sorted(stale), ["q0", "q1", "q2", "q3", "q4"])
        s.shutdown()

    def test_same_question_is_never_asked_twice(self):
        b = Blocker()
        s = ai_jobs.AiScheduler()
        j1 = s.submit("q", b, "q")
        j2 = s.submit("q", b, "q")
        self.assertIs(j1, j2)
        b.release.set()
        j1.future.result(timeout=2)
        time.sleep(0.05)
        self.assertEqual(b.started, ["q"])
        s.shutdown()

    def test_cancel_all_on_shutdown(self):
        b = Blocker()
        s = ai_jobs.AiScheduler()
        j = s.submit("q", b, "q")
        s.shutdown()
        self.assertTrue(j.token.cancelled)
        b.release.set()


# ---------------------------------------------------------------------------
# B + A - in the engine
# ---------------------------------------------------------------------------
class EngineBase(KeyPatch):
    def setUp(self):
        super().setUp()
        from puzzle_assistant import engine as E
        self.tmp = tempfile.mkdtemp()
        self.mem = Memory(os.path.join(self.tmp, "m.db"))
        self.cfg = Config()
        self.cfg.ai_provider, self.cfg.ai_model = "gemini", "m"
        self.eng = E.Engine(self.cfg, queue.Queue())
        self.eng.memory = self.mem
        self.diag = diagnostics.DiagnosticsLog(os.path.join(self.tmp, "diagnostics"))
        self.eng.diag = self.diag

    def tearDown(self):
        self.eng.stop()
        self.mem.close()

    def drain(self):
        out = []
        while not self.eng.out.empty():
            out.append(self.eng.out.get_nowait())
        return out

    def wait_done(self, st, limit=3.0):
        t0 = time.monotonic()
        while st.get("ai_job") and not st["ai_job"][1].done() and time.monotonic() - t0 < limit:
            time.sleep(0.01)


class GatedHttp:
    """Gemini replies; requests whose prompt contains a gate word wait until released."""

    def __init__(self):
        self.gates = {}
        self.calls = []

    def gate(self, word):
        self.gates[word] = threading.Event()
        return self.gates[word]

    def __call__(self, method, url, headers, body, timeout):
        prompt = body["contents"][0]["parts"][0]["text"]
        self.calls.append(prompt)
        for word, ev in self.gates.items():
            if word in prompt:
                ev.wait(5)
        letter = "B"
        return 200, gemini_reply({"answer": letter, "explanation": "x", "confidence": 0.9})


class EngineCancellation(EngineBase):
    def test_new_question_answered_while_old_request_hangs(self):
        http = GatedHttp()
        gate = http.gate("VIEJA")
        self.eng.ai = ai.AIAnswerer(self.cfg, self.mem, http)
        st = {"shown": None}
        q1 = Question("PREGUNTA", "pregunta VIEJA", ["a1", "b1"])
        q2 = Question("PREGUNTA", "pregunta nueva", ["a2", "b2"])
        self.eng._ask_ai(q1, ("p", "1"), {}, st)
        time.sleep(0.05)
        self.eng._ask_ai(q2, ("p", "2"), {}, st)              # the question changed
        t0 = time.monotonic()
        self.wait_done(st)
        self.assertLess(time.monotonic() - t0, 1.0)           # v3.3: waited for q1 (up to 8 s)
        self.eng._poll_ai(st)
        shown = [m for m in self.drain() if m.get("status") == "ai"]
        self.assertEqual([m["answer"].choice for m in shown], ["b2"])
        gate.set()                                            # q1's late answer arrives
        time.sleep(0.2)
        self.eng._poll_ai(st)
        self.assertEqual([m for m in self.drain() if m.get("status") == "ai"], [])
        outcomes = sorted(r["outcome"] for r in self.diag.read())
        self.assertEqual(outcomes, ["shown", "stale"])

    def test_memory_or_game_answer_stops_the_ai_request(self):
        http = GatedHttp()
        gate = http.gate("LENTA")
        self.eng.ai = ai.AIAnswerer(self.cfg, self.mem, http)
        st = {"shown": None}
        self.eng._ask_ai(Question("PREGUNTA", "pregunta LENTA", ["a", "b"]), ("p", "1"), {}, st)
        job = st["ai_job"][4]
        st["shown"] = (("p", "1"), "game")
        self.eng._drop_ai(st)                                 # e.g. the game revealed it
        self.assertTrue(job.token.cancelled)
        gate.set()
        job.future.result(timeout=2)
        time.sleep(0.05)
        self.assertEqual([r["outcome"] for r in self.diag.read()], ["stale"])

    def test_question_closed_cancels_a_waiting_request_before_it_is_sent(self):
        http = GatedHttp()
        g1, g2 = http.gate("UNO"), http.gate("DOS")
        self.eng.ai = ai.AIAnswerer(self.cfg, self.mem, http)
        sts = [{"shown": None} for _ in range(3)]
        for i, (word, st) in enumerate(zip(("UNO", "DOS", "TRES"), sts)):
            self.eng._ask_ai(Question("PREGUNTA", f"p {word}", ["a", "b"]), ("p", str(i)), {}, st)
            time.sleep(0.05)
        # UNO and DOS are running (both lanes busy, they ignore cancellation here);
        # TRES waits. Now TRES's question closes too:
        self.eng._drop_ai(sts[2])
        g1.set()
        g2.set()
        time.sleep(0.3)
        self.assertFalse(any("TRES" in p for p in http.calls))   # never sent
        self.assertEqual(sorted(r["outcome"] for r in self.diag.read()),
                         ["cancelled", "stale", "stale"])

    def test_no_second_call_after_web_search_for_a_closed_question(self):
        self.cfg.web_search, self.cfg.search_provider = True, "tavily"
        calls = []
        tok_holder = {}

        def http(method, url, headers, body, timeout):
            calls.append(url)
            if "tavily" in url:
                tok_holder["tok"].cancel()                     # question closed meanwhile
                return 200, {"results": [{"title": "t", "content": "c"}]}
            return 200, gemini_reply({"answer": "A", "confidence": 0.3})
        a = ai.AIAnswerer(self.cfg, self.mem, http)
        s = ai_jobs.AiScheduler()
        job = s.submit("q", a.answer, Question("PREGUNTA", "x", ["a", "b"]))
        tok_holder["tok"] = job.token
        res = job.future.result(timeout=2)
        self.assertEqual(sum(1 for u in calls if "generativelanguage" in u), 1)
        self.assertTrue(res.stale)
        s.shutdown()

    def test_no_web_search_for_a_question_that_closed_before_it(self):
        self.cfg.web_search, self.cfg.search_provider = True, "tavily"
        calls = []
        tok_holder = {}

        def http(method, url, headers, body, timeout):
            calls.append(url)
            if "generativelanguage" in url:
                tok_holder["tok"].cancel()                     # question closed during the AI call
            return 200, gemini_reply({"answer": "A", "confidence": 0.3})
        a = ai.AIAnswerer(self.cfg, self.mem, http)
        s = ai_jobs.AiScheduler()
        job = s.submit("q", a.answer, Question("PREGUNTA", "x", ["a", "b"]))
        tok_holder["tok"] = job.token
        job.future.result(timeout=2)
        self.assertEqual([u for u in calls if "tavily" in u], [])   # no search request wasted
        s.shutdown()

    def test_shown_time_includes_waiting(self):
        self.eng.ai = ai.AIAnswerer(self.cfg, self.mem, FakeHttp([], delay=0.3))
        st = {"shown": None}
        self.eng._ask_ai(Question("PREGUNTA", "x", ["a", "b"]), ("p", "1"), {}, st)
        self.wait_done(st)
        time.sleep(0.2)                                       # the screen check comes later
        self.eng._poll_ai(st)
        m = [m for m in self.drain() if m.get("status") == "ai"][0]
        waited = float(m["ai_info"].split("·")[1].strip().split()[0])
        self.assertGreaterEqual(waited, 0.5)
        rec = self.diag.read()[0]
        self.assertGreaterEqual(rec["total_ms"], 500)
        self.assertGreaterEqual(rec["request_ms"], 300)
        self.assertGreaterEqual(rec["poll_ms"], 150)


class FakeScreen:
    """The engine's screen: a quiz window is there until `shown` is cleared."""
    shown = True
    BOUNDS = {"left": 0, "top": 0, "width": 640, "height": 480}

    def __init__(self, monitor=0):
        self.screens = [self.BOUNDS]
        self.monitor = self.BOUNDS

    def targets(self, region):
        return [self.BOUNDS]

    def grab_bounds(self, b):
        import numpy as np
        return np.full((480, 640, 3), 22, np.uint8), self.BOUNDS

    def close(self):
        pass


@unittest.skipUnless(__import__("shutil").which("tesseract") or os.name == "nt",
                     "the engine loop needs Tesseract")
class EngineLoop(EngineBase):
    def test_closing_the_question_window_stops_the_ai_request(self):
        """v3.4 B: when the quiz window disappears while the AI is still thinking,
        the engine's own loop stops the request (Engine.run -> _drop_ai)."""
        import types
        from puzzle_assistant import engine as E
        from puzzle_assistant.ocr import find_tesseract
        if not find_tesseract(self.cfg):
            self.skipTest("Tesseract not installed")
        http = GatedHttp()
        gate = http.gate("CERRADA")
        self.eng.ai = ai.AIAnswerer(self.cfg, None, http)
        self.cfg.region, self.cfg.money_puzzle, self.cfg.world_atlas = None, False, False
        lay = types.SimpleNamespace(left=[], all_boxes=lambda: [(20, 100, 250, 37), (20, 150, 250, 37)])
        q = Question("PREGUNTA", "pregunta CERRADA", ["a", "b"])
        screen = types.SimpleNamespace(question=q, answered=False, progress=0, revealed=None,
                                       verdict="", text_unsure=False)
        FakeScreen.shown = True
        patches = [mock.patch.object(E, "ScreenGrabber", FakeScreen),
                   mock.patch.object(E, "locate_express", lambda *a, **k: None),
                   mock.patch.object(E, "locate_question", lambda f: lay if FakeScreen.shown else None),
                   mock.patch.object(E, "fingerprint", lambda f, l: 1),
                   mock.patch.object(E, "read_question", lambda *a, **k: screen)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        jobs = []
        submit = self.eng._ai_jobs.submit

        def spy(*args, **kw):
            job = submit(*args, **kw)
            jobs.append(job)
            return job
        self.eng._ai_jobs.submit = spy
        self.eng.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not http.calls:   # the AI has been asked
            time.sleep(0.05)
        self.assertTrue(http.calls, "the engine never asked the AI")
        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0].token.cancelled)
        FakeScreen.shown = False                                  # the player closes the window
        closed = time.monotonic()
        while time.monotonic() - closed < 2.0 and not jobs[0].token.cancelled:
            time.sleep(0.02)
        self.assertTrue(jobs[0].token.cancelled, "request not stopped within 2 s of the window closing")
        gate.set()
        jobs[0].future.result(timeout=5)
        time.sleep(0.1)
        self.assertEqual([r["outcome"] for r in self.diag.read()], ["stale"])


# ---------------------------------------------------------------------------
# A - the timing log
# ---------------------------------------------------------------------------
class TimingLog(EngineBase):
    def test_log_has_timings_only_no_text_no_key(self):
        q = Question("ADIVINANZA", "SECRETO Soy un boleto instantáneo", ["Una mesa OPCION1", "El raspadito"])
        self.eng.ai = ai.AIAnswerer(self.cfg, self.mem, FakeHttp(
            [(200, gemini_reply({"answer": "B", "explanation": "EXPLICACION", "confidence": 0.9})),
             (503, {"error": {"message": "MENSAJE high demand"}}), OSError("down")]))
        for i in range(3):
            st = {"shown": None}
            self.eng._ask_ai(q, ("a", str(i)), {}, st)
            self.wait_done(st)
            self.eng._poll_ai(st)
        with open(self.diag.path, encoding="utf-8") as fh:
            text = fh.read()
        for secret in ("SECRETO", "OPCION1", "raspadito", "EXPLICACION", "MENSAJE", KEY, "test-key"):
            self.assertNotIn(secret, text)
        rows = self.diag.read()
        self.assertEqual([r["outcome"] for r in rows], ["shown", "no_answer", "no_answer"])
        self.assertEqual([r["error"] for r in rows], ["", "overload", "connection"])
        self.assertEqual(rows[0]["category"], "ADIVINANZA")
        for r in rows:
            self.assertLessEqual(set(r), set(diagnostics.FIELDS) | {"ts"})

    def test_clean_drops_unknown_fields_and_free_text(self):
        rec = diagnostics.clean({"category": "Soy un boleto, ¿qué soy?", "question": "x", "key": KEY,
                                 "model": "gemini 3.5 <b>", "error": "Gemini error 503: text",
                                 "outcome": "shown", "total_ms": 812.345, "calls": True,
                                 "reused": "yes", "queue_ms": -5})
        self.assertEqual(rec, {"outcome": "shown", "total_ms": 812.3, "queue_ms": 0.0})

    def test_size_cap(self):
        d = diagnostics.DiagnosticsLog(os.path.join(self.tmp, "cap"), max_bytes=2000)
        for _ in range(200):
            d.log(outcome="shown", total_ms=700.0, provider="gemini", model="gemini-3.5-flash-lite")
        sizes = [os.path.getsize(p) for p in (d.path, d.old_path)]
        self.assertTrue(all(s <= 2000 for s in sizes), sizes)
        self.assertGreater(len(d.read()), 5)

    def test_unwritable_folder_never_raises(self):
        blocker = os.path.join(self.tmp, "file")
        open(blocker, "w").close()
        d = diagnostics.DiagnosticsLog(os.path.join(blocker, "sub"))   # parent is a file
        self.assertIsNone(d.log(outcome="shown"))

    def test_engine_logs_nothing_without_a_log_from_the_ui(self):
        from puzzle_assistant import engine as E
        eng = E.Engine(self.cfg, queue.Queue())
        self.assertIsNone(eng.diag)
        eng.stop()

    def test_error_kinds(self):
        import urllib.error
        k = ai.error_kind
        self.assertEqual(k(ai.AIUnavailable("rate", "rate", 429)), "rate")
        self.assertEqual(k(ai.AIUnavailable("x", ai._status_kind(503), 503)), "overload")
        self.assertEqual(ai._status_kind(404), "model_unavailable")
        self.assertEqual(ai._status_kind(403), "key")
        self.assertEqual(ai._status_kind(400, "API key not valid"), "key")
        self.assertEqual(ai._status_kind(400, "bad request"), "http")
        self.assertEqual(k(socket.timeout("t")), "timeout")
        self.assertEqual(k(urllib.error.URLError(socket.timeout("t"))), "timeout")
        self.assertEqual(k(ConnectionResetError()), "connection")
        self.assertEqual(k(transport.Aborted()), "stale")
        self.assertEqual(k(ai.AmbiguousReply("x")), "ambiguous")
        self.assertEqual(k(ValueError("x")), "bad_reply")


class Quota(KeyPatch):
    def test_daily_count_includes_a_stopped_request_that_was_sent(self):
        tmp = tempfile.mkdtemp()
        mem = Memory(os.path.join(tmp, "m.db"))
        a = ai_answerer(FakeHttp([transport.Aborted("x", sent=True)]), mem)
        res = a.answer(Question("PREGUNTA", "x", ["a", "b"]))
        self.assertTrue(res.stale)
        self.assertEqual(a._today(), 1)
        a2 = ai_answerer(FakeHttp([transport.Aborted("x", sent=False)]), mem)
        a2.answer(Question("PREGUNTA", "y", ["a", "b"]))
        self.assertEqual(a2._today(), 1)                     # never sent: not counted
        mem.close()


class ShutdownCounting(KeyPatch):
    def test_closed_memory_does_not_break_a_stopped_request(self):
        tmp = tempfile.mkdtemp()
        mem = Memory(os.path.join(tmp, "m.db"))

        def http(method, url, headers, body, timeout):
            mem.close()                                       # SK shuts down meanwhile
            raise transport.Aborted("x", sent=True)
        a = ai_answerer(http, mem)
        res = a.answer(Question("PREGUNTA", "x", ["a", "b"]))
        self.assertTrue(res.stale)
        self.assertEqual((res.provider, res.calls), ("gemini", 1))


if __name__ == "__main__":
    unittest.main()
