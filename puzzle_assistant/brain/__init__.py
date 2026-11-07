"""SK Brain: the knowledge and reasoning foundation shared by every part of
SK Assistant (future chat, puzzle solvers, World Atlas, Live Explorer...).

Independent of the user interface: no Tkinter, no threads of its own. The
application decides when to call it (e.g. from a background worker).

Stage B1 (this package): knowledge library schema, provenance / evidence /
status / version model, the API surface, and read-only adapters for the
existing quiz memory and World Atlas. Retrieval ranking (B2), reasoning
(B3), corrections workflow (B4), page importer (B5) and the test bench (B6)
build on these pieces.
"""
from .api import Brain  # noqa: F401

__all__ = ["Brain"]
