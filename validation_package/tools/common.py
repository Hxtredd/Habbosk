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
    """Refuse every network connection except loopback (the Brain makes no network calls)."""
    real_connect, real_ex = socket.socket.connect, socket.socket.connect_ex

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


def isolated_environment(tag):
    """A throwaway home for one run. Returns the env dict; nothing outside PKG is used."""
    root = os.path.join(AREA, tag)
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


def python_banner():
    import platform
    return f"Python {sys.version.split()[0]} on {sys.platform} ({platform.platform()})"
