"""Shared helpers for the SK Brain privacy validation package. Standard library only."""
import hashlib
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SOURCE = os.path.join(PKG, "source")
AREA = os.path.join(PKG, "test_area")          # EVERYTHING the tests write goes in here


def block_network():
    """Refuse network connections and name lookups except loopback (the Brain makes no network
    calls). A guard against accidents in this process, not a firewall: a child process that is
    not started through child_tests.py is not covered."""
    real_connect, real_ex = socket.socket.connect, socket.socket.connect_ex
    real_gai, real_sendto = socket.getaddrinfo, socket.socket.sendto

    def gai(host, *a, **k):
        if host not in (None, "", "localhost", "127.0.0.1", "::1"):
            raise OSError("name lookup blocked by the SK privacy validation package")
        return real_gai(host, *a, **k)

    def sendto(self, data, *a):
        addr = a[-1]
        if not ok(addr):
            raise OSError("network blocked by the SK privacy validation package")
        return real_sendto(self, data, *a)

    def ok(addr):
        host = addr[0] if isinstance(addr, tuple) else None
        return host in ("127.0.0.1", "::1", "localhost")

    def connect(self, addr):
        if not ok(addr) and self.family != getattr(socket, "AF_UNIX", -1):
            raise OSError("network blocked by the SK privacy validation package")
        return real_connect(self, addr)

    def connect_ex(self, addr):
        if not ok(addr) and self.family != getattr(socket, "AF_UNIX", -1):
            return 111
        return real_ex(self, addr)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo, socket.socket.sendto = gai, sendto


def network_selftest():
    """Proves the block works: a connection and a lookup to the outside must both be refused."""
    def conn():
        with socket.socket() as k:
            k.connect(("192.0.2.1", 80))
    for attempt in (conn, lambda: socket.getaddrinfo("example.com", 80)):
        try:
            attempt()
        except OSError as e:
            if "blocked by the SK" in str(e):
                continue
        return False                         # it got further than the block: not working
    return True


def isolated_environment(tag):
    """A throwaway home for one run (wiped first, so an earlier run cannot influence this one).
    Returns the env dict; nothing outside PKG is used."""
    import shutil
    root = os.path.join(AREA, tag)
    pkg_real = os.path.realpath(PKG)
    if os.path.commonpath([os.path.realpath(root), pkg_real]) != pkg_real:
        raise SystemExit("Refusing to clear a folder outside the package: " + root)
    shutil.rmtree(root, ignore_errors=True)
    if os.path.exists(root):
        raise SystemExit("Could not clear " + root + " - close any program using it and try again.")
    dirs = {k: os.path.join(root, k) for k in ("data", "tmp", "home", "appdata", "localappdata")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    env = dict(os.environ)
    for k in ("SK_ASSISTANT_DATA",):
        env[k] = dirs["data"]
    env.update(TEMP=dirs["tmp"], TMP=dirs["tmp"], TMPDIR=dirs["tmp"], HOME=dirs["home"],
               USERPROFILE=dirs["home"], APPDATA=dirs["appdata"],
               LOCALAPPDATA=dirs["localappdata"], PYTHONDONTWRITEBYTECODE="1")
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        env.pop(k, None)                                  # no provider keys in a test run
    return env


def apply_environment(env):
    os.environ.update(env)
    import tempfile
    tempfile.tempdir = env["TEMP"]


def file_list(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            p = os.path.join(d, f)
            out.append(os.path.relpath(p, root).replace(os.sep, "/"))
    return sorted(out)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def git_tree_sha1(entries) -> str:
    """entries: (name, mode_str, sha1_hex) for ONE directory level."""
    body = b""
    for name, mode, sha in sorted(entries, key=lambda e: e[0].encode() + (b"/" if e[1] == "40000" else b"")):
        body += mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha)
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def source_tree_sha(root, files):
    """The git TREE id of `root`, computed in pure Python (every file mode 100644, as in the
    commit). Files that .gitattributes marks 'eol=crlf' (*.bat, *.ps1, README.txt) are hashed with LF
    line ends: the repository stores them that way and only writes CRLF when it exports them."""
    nodes = {}
    for rel in files:
        data = open(os.path.join(root, rel), "rb").read()
        if rel.endswith((".bat", ".ps1")) or rel == "README.txt":   # .gitattributes: eol=crlf
            data = data.replace(b"\r\n", b"\n")
        parts = rel.split("/")
        d = nodes
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = git_blob_sha1(data)

    def tree(d):
        ents = []
        for name, v in d.items():
            if isinstance(v, dict):
                ents.append((name, "40000", tree(v)))
            else:
                ents.append((name, "100644", v))
        return git_tree_sha1(ents)
    return tree(nodes)


def python_warning():
    return None if sys.version_info[:2] == (3, 14) else (
        f"WARNING: this is Python {sys.version_info[0]}.{sys.version_info[1]}, not 3.14 - "
        "results are only valid for the Python you used earlier for the Brain checks.")


def python_banner():
    import platform
    return f"Python {sys.version.split()[0]} on {sys.platform} ({platform.platform()})"


def remove_compiled(root):
    """Delete compiled leftovers that the test run itself created (some tests start their own
    Python processes). Done only AFTER a run, never before: a pre-existing one is a failure.
    Returns anything in a __pycache__ folder that is NOT a .pyc file (reported, then removed)."""
    import shutil
    odd = []
    for d, dirs, fs in os.walk(root):
        for x in list(dirs):
            if x == "__pycache__":
                full = os.path.join(d, x)
                for dd, _, ff in os.walk(full):
                    odd += [os.path.join(dd, f) for f in ff if not f.endswith(".pyc")]
                shutil.rmtree(full, ignore_errors=True)
                dirs.remove(x)
    return odd


def error_kind(stderr: str):
    """Why did a test run fail? 'assertion' = a test noticed the change (AssertionError or our own
    NotShareable refusal); 'broken' = the harness or the edited code is simply broken
    (syntax / name / import errors ...); 'other' = some other exception."""
    import re
    blocks, cur = [], None
    for line in stderr.splitlines():
        if line.startswith("=" * 20):
            cur = []
            blocks.append(cur)
        elif line.startswith("-" * 20) and cur is not None and len(cur) > 2 and \
                any(l.startswith("Traceback") for l in cur):
            cur = None
        elif cur is not None:
            cur.append(line)
    kinds = []
    for b in blocks:
        top = [l for l in b if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|NotShareable)\b", l)]
        names = [re.match(r"^([\w.]+)", l).group(1).split(".")[-1] for l in top]
        if any(n in ("AssertionError", "NotShareable") for n in names):
            kinds.append("assertion")
        elif any(n in ("SyntaxError", "IndentationError", "NameError", "UnboundLocalError",
                       "ImportError", "ModuleNotFoundError") for n in names):
            kinds.append("broken")
        else:
            kinds.append("other")
    if not kinds:
        return "none"
    if "assertion" in kinds:
        return "assertion"
    return "broken" if "broken" in kinds else "other"
