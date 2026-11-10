"""HTTPS for the AI with a kept-open connection to Gemini (owner-approved change C).

v3.3 opened a new connection (TCP + TLS handshake) for every AI request.
KeepAliveHttp keeps one connection per background lane open to Gemini and
reuses it for the next request.

Safety rules:
  * Only Gemini (generativelanguage.googleapis.com) uses the kept-open
    connection; every other host (Claude, OpenAI, web search) keeps the v3.3
    method (urllib, a new connection each time).
  * When a proxy is configured on the PC (Windows Internet settings or the
    HTTPS_PROXY variable), the v3.3 method is used for everything: urllib
    knows how to go through proxies, this connection does not.
  * A kept-open connection that the server has closed in the meantime fails
    at once (reset / "remote end closed"): it is reopened and the request is
    sent again ONCE. A failure on a fresh connection is reported as before.
    A connection idle for more than MAX_IDLE seconds is not reused.
  * A cancelled request (ai_jobs.CancelToken) has its connection closed so it
    stops immediately; the connection is reopened for the next request.
  * Same interface as qa.ai.default_http: (method, url, headers, body,
    timeout) -> (status, json payload). Same timeout per network step.
"""
from __future__ import annotations

import http.client
import json
import select
import socket
import ssl
import threading
import time
import urllib.request
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from .ai_jobs import current_token

KEEPALIVE_HOSTS = frozenset({"generativelanguage.googleapis.com"})
MAX_IDLE = 50.0                 # seconds: older idle connections are not reused
PROXY_RECHECK = 60.0            # seconds between proxy checks

_info = threading.local()


class Aborted(OSError):
    """The request was cancelled (its question changed). sent: it had already
    gone out to the provider (then it still counts as a request)."""

    def __init__(self, msg: str = "request cancelled", sent: bool = False):
        super().__init__(msg)
        self.sent = sent


def last_call() -> Dict[str, object]:
    """How the latest request on THIS thread went: reused, reconnected, connect_ms, keepalive."""
    return dict(getattr(_info, "last", {}) or {})


def _set_info(**kw) -> None:
    _info.last = kw


def urllib_http(method: str, url: str, headers: Dict[str, str], body: Optional[dict],
                timeout: float) -> Tuple[int, dict]:
    """The v3.3 method: urllib, a new connection each time, system proxy honoured."""
    import urllib.error
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return exc.code, payload


def _decode(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


POLL = 0.1                      # seconds between checks while waiting for the reply


def wait_for_reply(conn, token, timeout: float) -> None:
    """Wait until the reply starts arriving, in short steps, so a cancelled
    request stops within POLL seconds (v3.4 B).

    Found on the owner's PC: on Windows neither shutdown() nor close() from
    another thread ends a thread that is already waiting for the reply
    (http.client keeps the socket open while its reader exists). So the
    request never waits in one long call: it checks the CancelToken every
    POLL seconds and only reads once data is there. Same time limit as
    before (the socket timeout). A reply that stalls after it has started
    is still ended by the socket timeout."""
    sock = getattr(conn, "sock", None)
    if token is None or sock is None:
        return
    try:
        sock.fileno()
    except Exception:                   # not a real socket (tests)
        return
    deadline = time.monotonic() + timeout
    while True:
        if token.cancelled:
            raise Aborted("request cancelled", sent=True)
        pending = getattr(sock, "pending", None)          # TLS bytes already decrypted
        if pending is not None and pending() > 0:
            return
        left = deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("timed out")
        try:
            ready, _, _ = select.select([sock], [], [], min(POLL, left))
        except (OSError, ValueError):
            if token.cancelled:
                raise Aborted("request cancelled", sent=True)
            raise
        if ready:
            return


_RETRY_ON_REUSED = (http.client.RemoteDisconnected, http.client.BadStatusLine,
                    http.client.ImproperConnectionState,
                    ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


class KeepAliveHttp:
    def __init__(self, fallback: Callable = urllib_http,
                 proxies: Callable[[], dict] = urllib.request.getproxies,
                 connection_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.fallback = fallback
        self._proxies = proxies
        self._factory = connection_factory or self._new_connection
        self._clock = clock
        self._tl = threading.local()            # one connection per lane (thread)
        self._proxy_seen: Optional[bool] = None
        self._proxy_at = -1e9
        self._ctx: Optional[ssl.SSLContext] = None

    # -- proxy detection ----------------------------------------------------
    def proxy_configured(self) -> bool:
        now = self._clock()
        if self._proxy_seen is None or now - self._proxy_at > PROXY_RECHECK:
            try:
                p = self._proxies() or {}
                self._proxy_seen = bool(p.get("https") or p.get("all"))
            except Exception:
                self._proxy_seen = True         # unsure: keep the v3.3 method
            self._proxy_at = now
        return bool(self._proxy_seen)

    # -- connections --------------------------------------------------------
    def _new_connection(self, host: str, timeout: float):
        if self._ctx is None:
            self._ctx = ssl.create_default_context()
        return http.client.HTTPSConnection(host, 443, timeout=timeout, context=self._ctx)

    def _conns(self) -> dict:
        d = getattr(self._tl, "conns", None)
        if d is None:
            d = self._tl.conns = {}
        return d

    def _get(self, host: str, timeout: float):
        """(connection, reused?)"""
        entry = self._conns().get(host)
        if entry is not None:
            conn, last_used = entry
            if self._clock() - last_used <= MAX_IDLE and getattr(conn, "sock", None) is not None:
                return conn, True
            self._drop(host)
        return self._factory(host, timeout), False

    def _drop(self, host: str) -> None:
        entry = self._conns().pop(host, None)
        if entry is not None:
            try:
                entry[0].close()
            except Exception:
                pass

    def close(self) -> None:
        """Close this thread's kept-open connections."""
        for host in list(self._conns()):
            self._drop(host)

    # -- one request --------------------------------------------------------
    def __call__(self, method: str, url: str, headers: Dict[str, str], body: Optional[dict],
                 timeout: float) -> Tuple[int, dict]:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or host not in KEEPALIVE_HOSTS or self.proxy_configured():
            _set_info(keepalive=False, reused=False, reconnected=False, connect_ms=0.0)
            return self.fallback(method, url, headers, body, timeout)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json", **headers}
        token = current_token()
        reconnected = False
        conn, reused = self._get(host, timeout)
        for attempt in (0, 1):
            if token is not None and token.cancelled:
                raise Aborted("request cancelled", sent=attempt > 0)
            connect_ms = 0.0
            if token is not None:
                token.attach(conn)
            try:
                if not reused:
                    t0 = time.perf_counter()
                    conn.connect()
                    connect_ms = (time.perf_counter() - t0) * 1000
                if getattr(conn, "sock", None) is not None:
                    conn.sock.settimeout(timeout)
                conn.request(method, path, body=data, headers=hdrs)
                wait_for_reply(conn, token, timeout)
                resp = conn.getresponse()
                raw = resp.read()
                status = resp.status
                if resp.will_close or (token is not None and token.cancelled):
                    self._drop(host)
                else:
                    self._conns()[host] = (conn, self._clock())
                _set_info(keepalive=True, reused=reused, reconnected=reconnected,
                          connect_ms=connect_ms)
                return status, _decode(raw)
            except _RETRY_ON_REUSED:
                self._drop(host)
                if token is not None and token.cancelled:
                    raise Aborted("request cancelled", sent=True)
                if reused and attempt == 0:     # the server had closed it: reopen once
                    conn, reused, reconnected = self._factory(host, timeout), False, True
                    continue
                raise
            except (OSError, http.client.HTTPException) as exc:
                self._drop(host)
                if token is not None and token.cancelled:
                    raise Aborted("request cancelled", sent=True) from exc
                if isinstance(exc, http.client.HTTPException) and not isinstance(exc, OSError):
                    raise OSError(f"HTTP protocol error ({exc.__class__.__name__})") from exc
                raise
            finally:
                if token is not None:
                    token.detach(conn)
        raise OSError("unreachable")            # pragma: no cover
