"""Running AI requests in the background without letting old questions block
new ones (owner-approved change B).

v3.3 had ONE background lane: when a question closed before its AI answer
arrived, the old request kept the lane busy (up to its 6-8 s time limit) and
the next question waited behind it.

Now:
  * The newest question always wins: submitting a request for a question
    cancels every request for other questions. A request that has not started
    yet is removed and never sent; a running one is stopped (its connection
    is closed, so it ends at once) and its result is ignored.
  * Strict limits, so the AI quota is never wasted: at most LANES (2) requests
    run at the same time and at most ONE request waits (always the newest -
    older waiting requests are cancelled). The second lane only exists so a
    new question never waits for an old request that is still stopping.
  * Every request carries a CancelToken; the AI code checks it before each
    call to the provider (no second call, e.g. after a web search, for a
    question that is gone).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, List, Optional

LANES = 2
_local = threading.local()


class CancelToken:
    """Set when the question a request is for has changed; the request checks
    it (transport._PollingRaw, AIAnswerer) and stops itself."""

    def __init__(self):
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


# Stopping a running request: transport._PollingRaw reads the reply in 0.1 s
# steps and checks the token between steps (Windows and Linux, TLS 1.2 and
# 1.3). Nothing is done to the socket from the cancelling thread: shutting an
# SSL socket down from another thread can make a concurrent send go out
# unencrypted (independent review), and on Windows it does not wake a waiting
# read anyway (owner's PC).


def current_token() -> Optional[CancelToken]:
    """The token of the request running on this thread (None outside a job)."""
    return getattr(_local, "token", None)


class AiJob:
    def __init__(self, ident, token: CancelToken):
        self.ident = ident
        self.token = token
        self.future: Future = Future()
        self.submitted = time.monotonic()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.logged = False                     # written to the timing log once
        self._log_lock = threading.Lock()
        self.meta: dict = {}                    # category / kind / timed, for the log

    def claim_log(self) -> bool:
        """True exactly once: the caller writes this job's timing-log line
        (the engine and the request's own thread may both try)."""
        with self._log_lock:
            if self.logged:
                return False
            self.logged = True
            return True

    @property
    def queue_ms(self) -> float:
        if self.started is None:
            return 0.0
        return (self.started - self.submitted) * 1000

    @property
    def run_ms(self) -> float:
        if self.started is None or self.finished is None:
            return 0.0
        return (self.finished - self.started) * 1000


class AiScheduler:
    """At most `lanes` running and one waiting request; the newest question wins."""

    def __init__(self, lanes: int = LANES, on_stale: Optional[Callable[[AiJob, object], None]] = None):
        self.lanes = lanes
        self._pool = ThreadPoolExecutor(max_workers=lanes, thread_name_prefix="ai")
        self._lock = threading.Lock()
        self._jobs: List[AiJob] = []
        self.on_stale = on_stale                # called once for a cancelled job

    def submit(self, ident, fn: Callable, *args) -> AiJob:
        with self._lock:
            self._jobs = [j for j in self._jobs if not j.future.done()]
            for j in self._jobs:
                if j.ident != ident:
                    self._cancel(j)
            same = [j for j in self._jobs if j.ident == ident and not j.token.cancelled]
            if same:                            # already asked: never twice
                return same[0]
            job = AiJob(ident, CancelToken())
            job.future = self._pool.submit(self._run, job, fn, args)
            self._jobs.append(job)
            return job

    def _run(self, job: AiJob, fn, args):
        job.started = time.monotonic()
        if job.token.cancelled:
            job.finished = job.started
            self._stale(job, None)
            return None
        _local.token = job.token
        result = None
        try:
            result = fn(*args)
            return result
        finally:
            _local.token = None
            job.finished = time.monotonic()
            if job.token.cancelled:
                self._stale(job, result)

    def _stale(self, job: AiJob, result) -> None:
        cb = self.on_stale
        if cb is None or not job.claim_log():
            return
        try:
            cb(job, result)
        except Exception:
            pass

    def _cancel(self, job: AiJob) -> None:
        job.token.cancel()
        if job.future.cancel():                 # had not started: never sent
            job.started = job.finished = time.monotonic()
            self._stale(job, None)
        elif job.finished is not None:          # had just finished: result ignored
            self._stale(job, None)

    def cancel(self, job: Optional[AiJob]) -> None:
        if job is None:
            return
        with self._lock:
            self._cancel(job)

    def cancel_all(self) -> None:
        with self._lock:
            for j in self._jobs:
                if not j.future.done():
                    self._cancel(j)
            self._jobs = []

    def active(self) -> int:
        """Requests running or waiting (for tests and diagnostics)."""
        with self._lock:
            return sum(1 for j in self._jobs if not j.future.done())

    def shutdown(self) -> None:
        self.cancel_all()
        self._pool.shutdown(wait=False)
