"""Opening a SQLite file read-only, safely for any folder name.

SQLite's read-only mode is asked for in a `file:` URI. In a URI `#`, `?` and
`%` mean something, so a folder like "José's #1 folder" used to cut the URI
short: the `?mode=ro` part was lost and SQLite created an empty file at the
cut-off path instead of opening the real one. Every character of the path is
escaped here, so the URI always names exactly the file and keeps `mode=ro`.
"""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote


def readonly_uri(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):            # Windows drive path: C:/... -> /C:/...
        p = "/" + p
    # empty authority ("file://") + path; a network path //server/share stays //server/share
    return "file://" + quote(p, safe="/:") + "?mode=ro"


def connect_readonly(path: str, **kwargs) -> sqlite3.Connection:
    """Read-only connection to an existing file (an error if it is missing:
    read-only mode never creates a file)."""
    return sqlite3.connect(readonly_uri(path), uri=True, **kwargs)
