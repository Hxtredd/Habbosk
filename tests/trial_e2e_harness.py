"""End-to-end run of the complete SK trial app (used by tests/test_trial.py).

    python trial_e2e_harness.py <trial root> <result.json>

Runs under a virtual X display (xvfb-run). Uses the trial launcher's real
isolation steps, the real SK window, engine, screen capture, OCR, memory,
World Atlas, AI code A-D and timing log. Only Gemini is replaced by a local
fake server (real sockets), and a "game" window shows real question
screenshots from tests/fixtures on a timeline:

  t= 3 s  Preguntas express question       -> AI request 1 (slow, 4 s)
  t= 7 s  a library question from memory   -> request 1 is stopped (stale)
  t=10 s  nothing on screen
  t=12 s  the express question again       -> request 2 answers (shown)
  t=15 s  nothing
  t=17 s  the express question again       -> request 3: HTTP 503 (no answer)
  t=20 s  nothing
  t=22 s  the express question again       -> request 4 hangs
  t=24 s  the SK window is closed          -> clean shutdown stops request 4
"""
import http.client
import http.server
import json
import os
import sys
import threading
import time

ROOT, RESULT = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(ROOT, "trial"))
import launcher  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "questions")
events = {"posts": [], "statuses": [], "aborted": 0}


class FakeGemini(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            events["aborted"] += 1

    def do_GET(self):
        self._send(200, {"models": [{"name": "models/gemini-3.5-flash-lite",
                                     "supportedGenerationMethods": ["generateContent"]}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        n = len(events["posts"]) + 1
        events["posts"].append({"n": n, "t": round(time.monotonic() - T0, 2), "key": self.headers.get("x-goog-api-key")})
        reply = {"candidates": [{"content": {"parts": [{"text": json.dumps(
            {"answer": "A", "explanation": "x", "confidence": 0.9})}]}}],
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 30}}
        if n == 1:
            time.sleep(4.0)
        elif n == 3:
            return self._send(503, {"error": {"message": "This model is currently experiencing high demand"}})
        elif n >= 4:
            time.sleep(30.0)
        self._send(200, reply)


T0 = time.monotonic()
cfg, guard_, ctx = launcher.prepare()
result = {"ctx_paths": {k: v for k, v in ctx["paths"].items()}, "settings": ctx["settings"]}

# the guard is live in this process: a deliberate attempt must be refused
probe = os.path.join(ctx["normal_dir"], "habbosk.db")
try:
    open(probe, "rb").close()
    result["probe"] = "NOT BLOCKED"
except PermissionError:
    result["probe"] = "blocked"
result["blocked_after_probe"] = guard_.blocked

class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                                     # stopped requests are expected here


srv = QuietServer(("127.0.0.1", 0), FakeGemini)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
from puzzle_assistant.qa import transport  # noqa: E402
transport.KeepAliveHttp._new_connection = (
    lambda self, host, timeout: http.client.HTTPConnection("127.0.0.1", port, timeout=timeout))

cfg.window_geometry = "440x360+20+20"
cfg.show_explorer = False
app = launcher.create_app(cfg)
result["title"] = app.root.title()
emit = app.engine._emit


def spy(**kw):
    if kw.get("state") == "question":
        events["statuses"].append({"t": round(time.monotonic() - T0, 2), "status": kw.get("status"),
                                   "category": getattr(kw.get("question"), "category", "")})
    emit(**kw)


app.engine._emit = spy

import tkinter as tk  # noqa: E402
from PIL import Image, ImageTk  # noqa: E402

game = tk.Toplevel(app.root)
game.overrideredirect(True)
game.geometry("1100x900+500+0")
game.configure(bg="#1b1b1f")
label = tk.Label(game, bg="#1b1b1f", bd=0)
label.place(x=150, y=150)
images = {name: ImageTk.PhotoImage(Image.open(os.path.join(FIX, name + ".png")))
          for name in ("express_open", "adivinanza_open")}


def show(name):
    if name:
        label.configure(image=images[name])
        label.image = images[name]
    else:
        label.configure(image="")
    events.setdefault("screen", []).append({"t": round(time.monotonic() - T0, 2), "show": name})


t_start = time.monotonic()
for at, name in ((3000, "express_open"), (7000, "adivinanza_open"), (10000, None),
                 (12000, "express_open"), (15000, None), (17000, "express_open"), (20000, None),
                 (22000, "express_open")):
    app.root.after(at, lambda n=name: show(n))
closed = {}


def close():
    closed["t"] = time.monotonic()
    app.close()


app.root.after(24000, close)
app.run()
result["threads_s"] = round(launcher.wait_for_threads(app), 2)
result["shutdown_s"] = round(time.monotonic() - closed.get("t", time.monotonic()), 2)
result["blocked_total"] = guard_.blocked
result.update(events)
with open(RESULT, "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=1, ensure_ascii=False)
sys.exit(0)
