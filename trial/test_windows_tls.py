"""Trial-only checks (never in the release ZIP): the key never travels
unencrypted when an outdated AI request is stopped, checked on the wire.

A local relay sits between SK's connection code and a local TLS 1.3 server
(test-only certificate) and records every byte in both directions. SK sends
a request with a test API key in its header, the request is stopped while
the server is still "thinking", and the recording must not contain the key
(or the request line) in readable form - only TLS records. Sockets are made
Windows-like for the cancel (shutdown/close from the cancelling thread do
nothing), so the request must stop by itself.
"""
import os
import socket
import ssl
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import test_ai_speed_v34 as base  # noqa: E402
from puzzle_assistant.qa import ai_jobs, transport  # noqa: E402
from puzzle_assistant.qa.model import Question  # noqa: E402

TLS_DIR = os.path.join(ROOT, "tests", "fixtures", "tls")


class Relay:
    """127.0.0.1 TCP relay that records all bytes it forwards."""

    def __init__(self, target_port):
        self.target = target_port
        self.data = bytearray()
        self.lock = threading.Lock()
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(8)
        self.port = self.ls.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.ls.accept()
            except OSError:
                return
            s = socket.create_connection(("127.0.0.1", self.target))
            for a, b in ((c, s), (s, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    def _pipe(self, src, dst):
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                with self.lock:
                    self.data += chunk
                dst.sendall(chunk)
        except OSError:
            pass
        for x in (src, dst):
            try:
                x.close()
            except OSError:
                pass

    def close(self):
        try:
            self.ls.close()
        except OSError:
            pass


@unittest.skipUnless(os.path.isfile(os.path.join(TLS_DIR, "localhost.pem")), "test TLS files missing")
class KeyNeverInClear(base.KeyPatch):
    @classmethod
    def setUpClass(cls):
        base.RealTLS13.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        base.RealTLS13.tearDownClass.__func__(cls)

    tls = True

    def setUp(self):
        super().setUp()
        base.LocalGemini.mode = "ok"
        self.relay = Relay(self.port)
        self.addCleanup(self.relay.close)
        cctx = ssl.create_default_context(cafile=os.path.join(TLS_DIR, "ca.pem"))
        port = self.relay.port

        def factory(host, t):
            return __import__("http.client").client.HTTPSConnection("127.0.0.1", port, timeout=t, context=cctx)
        self.http = transport.KeepAliveHttp(proxies=lambda: {}, connection_factory=factory)
        self.addCleanup(self.http.close)

    def assert_no_clear_text(self):
        with self.relay.lock:
            wire = bytes(self.relay.data)
        self.assertGreater(len(wire), 500)                         # TLS traffic was recorded
        self.assertEqual(wire[:1], b"\x16")                          # starts with a TLS handshake record
        for secret in (base.KEY.encode(), b"x-goog-api-key", b"POST /v1beta", b"generateContent",
                       b"PREGUNTA"):
            self.assertNotIn(secret, wire, secret)

    def test_normal_request_is_encrypted(self):
        a = base.ai_answerer(self.http)
        r = a.answer(Question("PREGUNTA", "x", ["A1", "B1"]))
        self.assertEqual(r.answer.choice, "B1")
        self.assert_no_clear_text()

    def test_stopping_a_request_never_sends_the_key_in_clear(self):
        sched = ai_jobs.AiScheduler()
        a = base.ai_answerer(self.http)
        q = Question("PREGUNTA", "x", ["A1", "B1"])
        self.assertIsNotNone(sched.submit("q0", a.answer, q, 8.0).future.result(timeout=5).answer)
        base.LocalGemini.mode = "hang"
        for i in range(5):                                           # stop several requests mid-way
            job = sched.submit(f"q{i + 1}", a.answer, q, 8.0)
            time.sleep(0.15 + 0.1 * i)
            with base.windows_like():
                t0 = time.monotonic()
                sched.cancel(job)
                res = job.future.result(timeout=3)
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertTrue(res.stale)
        sched.shutdown()
        time.sleep(0.2)
        self.assert_no_clear_text()


if __name__ == "__main__":
    unittest.main()
