"""B6 sources and passages: imported files, exact quotations, provenance.

Everything here works on ONE library database through a KnowledgeStore
(a person's session: their personal file, with shared.db read-only; or a
moderator's shared file). brain/library.py exposes it as session methods.

The rules (the B6 decisions):
  * an imported file is stored as VERSIONS of a source; a version's canonical
    text is stored once and a passage is a position range into it, so a
    passage's text is always text[start:end];
  * a quotation from an imported source is evidence only if it is found
    EXACTLY (after NFC + line endings) inside one passage of the CURRENT
    version (cite() refuses anything else); a new version leaves earlier
    quotations pointing at the old one ("source_changed");
  * support gates (evidence_check): current version, exact text at the stored
    position, inside one sentence / list item, the passage not flagged
    (invisible / mixed-script characters), the author not "self" and not
    "ai_generated"; for Documented answers the WHOLE passage must state the
    fact too, so a fragment cannot drop a negation or a hedge;
  * provenance: the importer and time are recorded by SK; author, publisher,
    reference and date are DECLARED; a person may ATTEST authorship /
    authenticity. Attestation never makes anything true and never changes a
    claim's status - only a person's review of the claim does;
  * passages never change an established answer: a passage that says
    otherwise is shown as a CHALLENGE (grouped by publisher / material, so
    many copies count once); only a documented (unreviewed) answer becomes
    "the sources disagree";
  * suggestions read from one's own passages are only suggestions: nothing
    is learnt until the owner accepts one (an unverified personal claim);
  * a version can be purged by its owner: its text, passages, search index
    and facts are securely deleted.
No AI, no network, standard library only.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import urllib.parse
from typing import Dict, List, Optional

from . import importer
from .facts import display as _display
from .facts import extract
from .passages import SOURCE_FLAGS, SUPPORT_KINDS, split
from .store import BrainError, _now
from .text import index_text, terms

AUTHOR_KINDS = ("named", "unknown", "self", "ai_generated")
NOT_EVIDENCE = ("self", "ai_generated")
_SUPPORT = ("direct", "confirmation")


# ------------------------------------------------------------------ helpers
@contextlib.contextmanager
def _tx(store):
    """All or nothing, like store writes (commits once, at the outermost level)."""
    with store._lock:
        store._depth += 1
        try:
            yield
            if store._depth == 1 and store._db.in_transaction:
                store._db.commit()
        except BaseException:
            if store._depth == 1 and store._db.in_transaction:
                store._db.rollback()
            raise
        finally:
            store._depth -= 1


def strip_reference(ref: Optional[str], keep_query: bool = False) -> Optional[str]:
    """A declared reference without its query string and fragment (they often
    carry session ids, tracking or search words). Never fetched."""
    if not ref:
        return ref
    ref = ref.strip()
    if keep_query:
        return ref.split("#", 1)[0]
    try:
        u = urllib.parse.urlsplit(ref)
    except ValueError:
        u = None
    if u is not None and u.scheme and u.netloc:
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", ""))
    return ref.split("#", 1)[0].split("?", 1)[0]


def _has_fts(store, schema: str = "main") -> bool:
    return store._db.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE name='passage_fts'"
                             ).fetchone() is not None


def _b6(store) -> bool:
    return store.role != "file"


def label(v: Optional[dict]) -> str:
    """How a version's origin is shown, in plain words."""
    if v is None:
        return "not an imported source"
    ak = v["author_kind"]
    if ak == "ai_generated":
        return "AI-generated - never evidence"
    if ak == "self":
        return "written by the person who imported it - never evidence"
    who = f"author {v['author_name']}" if ak == "named" and v.get("author_name") else \
        "author unknown"
    pub = f", publisher {v['publisher']}" if v.get("publisher") else ""
    fl = _flags(v.get("flags"))
    html = "html_not_rendered" in fl
    if html:
        pub += " - unrendered HTML: reference only, never evidence or a challenge"
    pdf_warn = sorted(f for f in fl if f.startswith("pdf_") and ":" not in f
                      and f not in PDF_INFO)
    if pdf_warn:
        pub += (" - PDF text SK could not confirm as shown on its page (" +
                ", ".join(w[4:].replace("_", " ") for w in pdf_warn) +
                "): reference only, never evidence or a challenge")
    ref = [f.split(":", 1)[1] for f in fl if f.startswith("pdf_reference_only_pages:")]
    if ref:
        pub += f" - PDF pages {ref[0]} are reference only"
    scan = [f.split(":", 1)[1] for f in fl if f.startswith("pdf_image_only_pages:")]
    if scan:
        pub += f" - PDF pages {scan[0]} are images only (OCR is not supported)"
    if v.get("provenance") == "attested":
        return f"{who}{pub} (authorship attested by a person - not a check of the facts)"
    return f"{who}{pub} (declared, not attested)"


PDF_INFO = ("pdf_incremental_updates", "pdf_javascript", "pdf_actions",
            "pdf_form_fields_ignored", "pdf_annotations_ignored")


def _flags(raw) -> tuple:
    try:
        return tuple(json.loads(raw or "[]"))
    except (TypeError, ValueError):
        return ("unreadable",)


def current_version(store, source_id: int) -> Optional[dict]:
    r = store._db.execute("SELECT * FROM source_versions WHERE source_id=? AND state='current'",
                          (source_id,)).fetchone()
    return dict(r) if r else None


def versions(store, source_id: int) -> List[dict]:
    """A source's versions (without their text)."""
    return [dict(r) for r in store._db.execute(
        "SELECT id, source_id, version, fmt, size, author_kind, author_name, publisher, "
        "reference, captured_at, provenance, attested_by, attested_at, imported_by, "
        "imported_at, flags, state FROM source_versions WHERE source_id=? ORDER BY version",
        (source_id,))]


def is_imported(store, source_id: Optional[int]) -> bool:
    if source_id is None or not _b6(store):
        return False
    return store._db.execute("SELECT 1 FROM source_versions WHERE source_id=? LIMIT 1",
                             (source_id,)).fetchone() is not None


class _Text:
    """A Parsed-like view of a stored version (text + blocks) for passages.split."""

    def __init__(self, text: str, structure: str, flags=()):
        self.text = text
        self.flags = tuple(flags or ())
        blocks = []
        for b in json.loads(structure or "[]"):
            k, s, e, h, lv = b[:5]
            extra = {}
            if len(b) >= 8:                            # PDF: page, box, page warnings
                extra = {"page": int(b[5]), "bbox": tuple(b[6]), "flags": tuple(b[7])}
            blocks.append(importer.Block(k, s, e, h, lv, **extra))
        self.blocks = tuple(blocks)


def _structure(parsed) -> str:
    return json.dumps([[b.kind, b.start, b.end, b.heading_path, b.level] +
                       ([b.page, list(b.bbox), list(b.flags)] if getattr(b, "page", 0) else [])
                       for b in parsed.blocks], ensure_ascii=False)


def store_passages(store, version_id: int, source_id: int, parsed, sha=None) -> int:
    """The passages of one version, their search words and facts (main file)."""
    text = parsed.text
    fts = _has_fts(store)
    n = 0
    for p in split(parsed):
        seg = text[p.start:p.end]
        pid = store._new("passages", "INSERT INTO main.passages (version_id, source_id, ordinal, "
                         "kind, start_pos, end_pos, heading_path, sha, flags) VALUES "
                         "(?,?,?,?,?,?,?,?,?)",
                         (version_id, source_id, p.ordinal, p.kind, p.start, p.end,
                          p.heading_path, sha(p.sha) if sha else p.sha,
                          json.dumps(list(p.flags)))).lastrowid
        words = index_text(seg)
        store._db.execute("INSERT INTO main.passage_index VALUES (?,?)", (pid, words))
        if fts:
            store._db.execute("INSERT INTO main.passage_fts(rowid, terms) VALUES (?,?)",
                              (pid, words))
        if p.kind in SUPPORT_KINDS and not p.flags:
            f = extract(seg)
            if f is not None:
                store._db.execute("INSERT INTO main.passage_facts VALUES (?,?,?,?,?,?,?)",
                                  (pid, f.relation, f.place, f.value, int(f.absent),
                                   f.qualifier, f.display))
        n += 1
    return n


def delete_passages(store, where: str, args, schema: str = "main", con=None) -> None:
    """Securely remove passages (and everything derived from them) matching
    `where` (on the passages table)."""
    db = con if con is not None else store._db
    ids = [r[0] for r in db.execute(f"SELECT id FROM {schema}.passages WHERE {where}", args)]
    fts = db.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE name='passage_fts'"
                     ).fetchone() is not None
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for t in ("passage_index", "passage_facts"):
            db.execute(f"DELETE FROM {schema}.{t} WHERE passage_id IN ({q})", chunk)
        if fts:
            db.execute(f"DELETE FROM {schema}.passage_fts WHERE rowid IN ({q})", chunk)
        db.execute(f"DELETE FROM {schema}.passages WHERE id IN ({q})", chunk)


def scrub_search_index(con, schema: str = "main") -> None:
    """After deleting passages: merge the search index so no old segment is
    left (FTS5 secure-delete already removed the entries; 'optimize' rewrites
    what is left; the freed pages are zeroed by PRAGMA secure_delete)."""
    if con.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE name='passage_fts'").fetchone():
        con.execute(f"INSERT INTO {schema}.passage_fts(passage_fts) VALUES('optimize')")


# ------------------------------------------------------------------ importing
def _own_source(store, source_id: int) -> dict:
    r = store._db.execute("SELECT * FROM main.sources WHERE id=?", (source_id,)).fetchone()
    if r is None or store.role != "personal" or r["added_by"] != store.owner:
        raise BrainError("not one of your sources")
    return dict(r)


def import_file(store, path: str, *, author_kind: str, author_name: str = "",
                publisher: str = "", reference: str = "", captured_at: str = "",
                title: str = "", private: bool = False, fmt: Optional[str] = None,
                keep_query: bool = False) -> dict:
    """Import a local .txt / .md / .html file as a new source (version 1), in
    the person's own file. author_kind is DECLARED: named | unknown | self |
    ai_generated (self and AI-generated material is never evidence)."""
    if store.role != "personal":
        raise BrainError("files are imported into a person's own file")
    if author_kind not in AUTHOR_KINDS:
        raise BrainError(f"author_kind is one of {', '.join(AUTHOR_KINDS)}")
    if author_kind == "named" and not (author_name or "").strip():
        raise BrainError("a named author needs a name (or declare the author unknown)")
    parsed = importer.import_file(path, fmt)        # refused before anything is stored
    now = _now()
    with _tx(store):
        sid = store.add_source("import_file", title=title or os.path.basename(path),
                               reference=strip_reference(reference, keep_query) or "",
                               content_hash=parsed.text_sha, captured_at=captured_at or None,
                               private=private, by=store.owner)
        vid = _new_version(store, sid, 1, parsed, author_kind, author_name, publisher,
                           strip_reference(reference, keep_query), captured_at, now)
        store._db.execute("INSERT INTO main.source_files VALUES (?,?)",
                          (sid, os.path.abspath(path)))
        n = store._db.execute("SELECT COUNT(*) FROM main.passages WHERE version_id=?",
                              (vid,)).fetchone()[0]
    return {"source_id": sid, "version": 1, "passages": n, "flags": list(parsed.flags)}


def _new_version(store, sid, number, parsed, author_kind, author_name, publisher, reference,
                 captured_at, now) -> int:
    vid = store._new("source_versions", "INSERT INTO main.source_versions (source_id, version, "
                     "fmt, text, text_tag, bytes_tag, size, structure, author_kind, author_name, "
                     "publisher, reference, captured_at, provenance, imported_by, imported_at, "
                     "flags, state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'declared',?,?,?,'current')",
                     (sid, number, parsed.fmt, parsed.text, parsed.text_sha, parsed.bytes_sha,
                      len(parsed.text), _structure(parsed), author_kind, author_name or None,
                      publisher or None, reference or None, captured_at or None, store.owner,
                      now, json.dumps(list(parsed.flags)))).lastrowid
    store_passages(store, vid, sid, parsed)
    return vid


def _claims_on(store, source_id: int, version_ids) -> List[int]:
    if not version_ids:
        return []
    q = ",".join("?" * len(version_ids))
    return sorted({r[0] for r in store._db.execute(
        f"SELECT e.claim_id FROM main.evidence e JOIN main.evidence_locations l ON "
        f"l.evidence_id=e.id WHERE e.source_id=? AND l.version_id IN ({q})",
        [source_id] + list(version_ids))})


def reimport(store, source_id: int, path: Optional[str] = None) -> dict:
    """Read a source's file again. Unchanged text: nothing happens. Changed
    text: a NEW version (same declared author - one declaration per source);
    the earlier one is kept, marked superseded. Quotations located in the
    earlier version no longer count (source_changed) until cited again."""
    src = _own_source(store, source_id)
    cur = current_version(store, source_id)
    if cur is None:
        raise BrainError("this source has no current imported version")
    if path is None:
        r = store._db.execute("SELECT path FROM main.source_files WHERE source_id=?",
                              (source_id,)).fetchone()
        if r is None:
            raise BrainError("the file of this source is not known; give its path")
        path = r[0]
    parsed = importer.import_file(path, cur["fmt"])
    if parsed.text_sha == cur["text_tag"]:
        return {"state": "unchanged", "version": cur["version"], "affected_claims": []}
    now = _now()
    with _tx(store):
        store._db.execute("UPDATE main.source_versions SET state='superseded' WHERE source_id=? "
                          "AND state='current'", (source_id,))
        n = cur["version"] + 1
        _new_version(store, source_id, n, parsed, cur["author_kind"], cur["author_name"],
                     cur["publisher"], cur["reference"], cur["captured_at"], now)
        store._db.execute("UPDATE main.sources SET content_hash=? WHERE id=?",
                          (parsed.text_sha, src["id"]))
        store._db.execute("INSERT OR REPLACE INTO main.source_files VALUES (?,?)",
                          (source_id, os.path.abspath(path)))
        old = [r[0] for r in store._db.execute(
            "SELECT id FROM main.source_versions WHERE source_id=? AND state!='current'",
            (source_id,))]
        affected = _claims_on(store, source_id, old)
        if _has_fts(store):                  # search shows current versions only
            store._db.execute(f"DELETE FROM main.passage_fts WHERE rowid IN (SELECT id FROM "
                              f"main.passages WHERE version_id IN ({','.join('?' * len(old))}))",
                              old)
        for cid in affected:
            store._event(cid, "source_changed", None, None, store.owner,
                         f"source {source_id} has a new version ({n})")
    return {"state": "new_version", "version": n, "affected_claims": affected}


# ------------------------------------------------------------------ citing
def locate(store, version: dict, quote: str) -> Optional[dict]:
    """The first exact occurrence of `quote` lying inside ONE passage of the
    version, as {start, end, passage}; None if there is none."""
    text = version["text"]
    if not text or not quote:
        return None
    i = text.find(quote)
    while i != -1:
        e = i + len(quote)
        p = store._db.execute("SELECT * FROM passages WHERE version_id=? AND start_pos<=? AND "
                              "end_pos>=? ORDER BY ordinal LIMIT 1",
                              (version["id"], i, e)).fetchone()
        if p is not None:
            return {"start": i, "end": e, "passage": dict(p)}
        i = text.find(quote, i + 1)
    return None


def clean_quote(quote: str) -> str:
    """The approved cleaning only: NFC + line endings. Nothing is trimmed."""
    return importer.canonical(quote or "")


def cite(store, claim_id: int, source_id: int, quote: str, kind: str = "direct") -> dict:
    """Quote one of YOUR imported sources as evidence for one of your claims.
    The quote must be found exactly (after NFC + line endings) inside one
    passage of the source's current version - otherwise nothing is stored.
    AI-generated sources are never evidence; your own writing (author self)
    can only be recorded as an assertion."""
    _own_source(store, source_id)
    v = current_version(store, source_id)
    if v is None or v["text"] is None:
        raise BrainError("this source has no current imported text to quote")
    if v["author_kind"] == "ai_generated":
        raise BrainError("AI-generated material is never evidence")
    if v["author_kind"] == "self" and kind != "assertion":
        raise BrainError("your own writing is not evidence; it can only be recorded as an "
                         "assertion")
    q = clean_quote(quote)
    loc = locate(store, v, q)
    if loc is None:
        raise BrainError("the quote was not found exactly inside one passage of the current "
                         "version - nothing was stored")
    import hashlib
    with _tx(store):
        eid = store.add_evidence(claim_id, kind, source_id, q, by=store.owner, _located=True)
        store._db.execute("INSERT INTO main.evidence_locations VALUES (?,?,?,?,?,?)",
                          (eid, v["id"], loc["passage"]["id"], loc["start"], loc["end"],
                           hashlib.sha256(q.encode("utf-8")).hexdigest()))
    claim = store._claim(claim_id)
    ok, why = evidence_check(store, eid, claim, strict=True)
    out = {"evidence_id": eid, "passage_kind": loc["passage"]["kind"], "supports": ok,
           "why": why}
    if v["fmt"] == "pdf":
        from .pdf_import import page_of
        pg = page_of(v["structure"], loc["start"], loc["end"])
        out["page"], out["bbox"] = (pg["page"], pg["bbox"]) if pg else (None, None)
    return out


# ------------------------------------------------------------------ the gates
def evidence_check(store, evidence_id: int, claim: Optional[dict], strict: bool):
    """(counts, why) for one evidence row of an imported source.
    strict=False: the evidence is real (counts towards verification by a
    person); strict=True: it also states the claim's fact by itself, and so
    does the whole passage it lies in (Documented answers)."""
    from .respond import quote_supports
    e = store._db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
    if e is None or claim is None:
        return False, "missing"
    if e["kind"] not in _SUPPORT:
        return False, "not_support_kind"
    quote = e["locator"] or ""
    v_any = store._db.execute("SELECT * FROM source_versions WHERE source_id=? ORDER BY version "
                              "DESC LIMIT 1", (e["source_id"],)).fetchone()
    loc = store._db.execute("SELECT * FROM evidence_locations WHERE evidence_id=?",
                            (evidence_id,)).fetchone()
    if loc is None:
        # shared copy granted with the quote only: no text here to check against.
        # It counts only with the owner's file's STRICT check of the whole passage
        # for exactly this claim's wording, recorded when it was proposed and
        # copied with the approval (quote_checks) - never on the quote alone
        if v_any is None or v_any["text"] is not None or v_any["state"] != "current":
            return False, "not_located"
        if v_any["author_kind"] in NOT_EVIDENCE:
            return False, v_any["author_kind"]
        if any(f in _flags(v_any["flags"]) for f in SOURCE_FLAGS):
            return False, "source_flagged"
        if not terms(quote):
            return False, "no_quote"
        chk = store._db.execute("SELECT * FROM quote_checks WHERE evidence_id=?",
                                (evidence_id,)).fetchone()
        if chk is None:
            return False, "passage_not_checked"
        if chk["passage_kind"] not in SUPPORT_KINDS or _flags(chk["passage_flags"]):
            return False, "passage_kind_or_flags"
        from .store import _text_sha
        if chk["claim_sha"] != _text_sha(claim["text"], claim.get("subject") or ""):
            return False, "checked_for_other_wording"
        return True, "quote_only"
    v = store._db.execute("SELECT * FROM source_versions WHERE id=?",
                          (loc["version_id"],)).fetchone()
    if v is None or v["state"] != "current" or v["text"] is None:
        return False, "source_changed"
    if v["author_kind"] in NOT_EVIDENCE:
        return False, v["author_kind"]
    if any(f in _flags(v["flags"]) for f in SOURCE_FLAGS):
        return False, "source_flagged"             # e.g. unrendered HTML
    if v["text"][loc["start_pos"]:loc["end_pos"]] != quote or not quote:
        return False, "quote_not_at_location"
    p = store._db.execute("SELECT * FROM passages WHERE id=?", (loc["passage_id"],)).fetchone()
    if p is None or p["version_id"] != v["id"] or not (
            p["start_pos"] <= loc["start_pos"] and loc["end_pos"] <= p["end_pos"]):
        return False, "not_inside_one_passage"
    if p["kind"] not in SUPPORT_KINDS:
        return False, "passage_kind_" + p["kind"]
    if json.loads(p["flags"] or "[]"):
        return False, "passage_flagged"
    if strict:
        whole = v["text"][p["start_pos"]:p["end_pos"]]
        subj = claim.get("subject") or ""
        # the passage is what is read: IT must state the fact (all of it, and
        # nothing else); the quote is the exact part of it that was pointed at
        if not (terms(quote) and quote_supports(claim["text"], subj, whole) and
                tight(claim, whole)):
            return False, "quote_does_not_state_it"
    return True, "located"


_RELATION_WORDS = None


def _relation_words() -> frozenset:
    """Every word written in B3's built-in sentence forms."""
    global _RELATION_WORDS
    if _RELATION_WORDS is None:
        import re
        from . import facts
        words = set()
        for _, rx, _ in facts._P:
            words.update(re.findall(r"[^\W\d_]{2,}", rx.pattern))
        _RELATION_WORDS = frozenset(terms(" ".join(sorted(words))))
    return _RELATION_WORDS


def tight(claim: dict, passage: str) -> bool:
    """B6, stricter than B3's reading: the passage says nothing BESIDES the fact
    - no word outside the claim's own words and the relation's sentence forms
    ("... during the festival", "... and the Clock Tower has room ID 43"), and
    it is not a question."""
    t = passage.strip().rstrip("\"'\u201d\u2019\u00bb)]")
    if t.endswith("?") or t.startswith("\u00bf"):
        return False
    allowed = set(terms(claim.get("text") or "")) | set(terms(claim.get("subject") or "")) | \
        _relation_words()
    return set(terms(passage)) <= allowed


def evidence_info(store, evidence_id: int) -> Optional[dict]:
    """The version an evidence row rests on (for summaries)."""
    loc = store._db.execute("SELECT version_id FROM evidence_locations WHERE evidence_id=?",
                            (evidence_id,)).fetchone()
    if loc is not None:
        r = store._db.execute("SELECT * FROM source_versions WHERE id=?", (loc[0],)).fetchone()
    else:
        r = store._db.execute("SELECT v.* FROM evidence e JOIN source_versions v ON "
                              "v.source_id=e.source_id WHERE e.id=? ORDER BY v.version DESC "
                              "LIMIT 1", (evidence_id,)).fetchone()
    return dict(r) if r else None


def source_changed(store, claim_id: int) -> bool:
    """Some of the claim's quotations point at a version that is no longer current."""
    if not _b6(store):
        return False
    return store._db.execute(
        "SELECT 1 FROM evidence e JOIN evidence_locations l ON l.evidence_id=e.id JOIN "
        "source_versions v ON v.id=l.version_id WHERE e.claim_id=? AND e.kind IN "
        "('direct','confirmation') AND v.state!='current' LIMIT 1", (claim_id,)
    ).fetchone() is not None


# ------------------------------------------------------------------ provenance
def attest(store, source_id: int, who: str, note: str = "") -> dict:
    """A person attests the AUTHORSHIP / AUTHENTICITY of a source's current
    version (e.g. "I checked this is the publisher's own page"). It does not
    check the facts in it and changes no claim's status."""
    if not (note or "").strip():
        raise BrainError("say how the authorship was checked (a note is required)")
    if store.role == "personal":
        _own_source(store, source_id)
    elif store.role != "shared":
        raise BrainError("attestation is for library files")
    v = store._db.execute("SELECT * FROM main.source_versions WHERE source_id=? AND "
                          "state='current'", (source_id,)).fetchone()
    if v is None:
        raise BrainError("this source has no current imported version")
    if v["author_kind"] in NOT_EVIDENCE:
        raise BrainError("self-written or AI-generated material cannot be attested as evidence")
    with _tx(store):
        store._db.execute("UPDATE main.source_versions SET provenance='attested', attested_by=?, "
                          "attested_at=?, attest_note=? WHERE id=?",
                          (who, _now(), note.strip(), v["id"]))
    return {"source_id": source_id, "version": v["version"], "provenance": "attested"}


# ------------------------------------------------------------------ purging
def purge_version(store, source_id: int, version: int) -> dict:
    """The owner removes one version's text for good: its text, passages,
    search words and facts are securely deleted (the row stays, marked
    purged, so the history says a version existed). Quotations already cited
    from it stay in your evidence (they are your own record) - listed."""
    _own_source(store, source_id)
    v = store._db.execute("SELECT * FROM main.source_versions WHERE source_id=? AND version=?",
                          (source_id, version)).fetchone()
    if v is None:
        raise BrainError("no such version")
    if v["state"] == "purged":
        return {"state": "already_purged", "still_quoted": []}
    with _tx(store):
        delete_passages(store, "version_id=?", (v["id"],))
        store._db.execute("UPDATE main.source_versions SET text=NULL, bytes_tag=NULL, "
                          "structure='[]', state='purged' WHERE id=?", (v["id"],))
        quoted = [r[0] for r in store._db.execute(
            "SELECT evidence_id FROM main.evidence_locations WHERE version_id=?", (v["id"],))]
        for cid in _claims_on(store, source_id, [v["id"]]):
            store._event(cid, "source_changed", None, None, store.owner,
                         f"source {source_id} version {version} was purged")
        if v["state"] == "current":
            store._db.execute("DELETE FROM main.source_files WHERE source_id=?", (source_id,))
    with store._lock:
        scrub_search_index(store._db)
        store._db.commit()
        from .library import checkpoint
        done = checkpoint(store._db)
    return {"state": "purged" if done else "purged_log_busy", "still_quoted": quoted}


# ------------------------------------------------------------------ suggestions
def suggestions(store, limit: int = 100) -> List[dict]:
    """Facts SK can read in YOUR passages that none of your claims states yet
    (at most `limit` open at a time). Only suggestions: nothing is learnt until
    you accept one."""
    if store.role != "personal":
        return []
    with _tx(store):
        room = limit - store._db.execute("SELECT COUNT(*) FROM main.claim_candidates WHERE "
                                         "state='open'").fetchone()[0]
        have = {(r[0], r[1], r[2], r[3], r[4]) for r in store._db.execute(
            "SELECT f.relation, f.place, f.value, f.absent, f.qualifier FROM main.claim_facts f "
            "JOIN main.claims c ON c.id=f.claim_id WHERE f.relation IS NOT NULL")}
        rows = store._db.execute(
            "SELECT p.id, f.relation, f.place, f.value, f.absent, f.qualifier FROM main.passages p "
            "JOIN main.passage_facts f ON f.passage_id=p.id JOIN main.source_versions v ON "
            "v.id=p.version_id JOIN main.sources s ON s.id=p.source_id WHERE v.state='current' "
            "AND s.added_by=? AND v.author_kind NOT IN ('self','ai_generated') AND p.flags='[]' "
            "AND p.id NOT IN (SELECT passage_id FROM main.claim_candidates) ORDER BY p.id",
            (store.owner,)).fetchall()
        for r in rows:
            if room <= 0:
                break
            if tuple(r[1:]) in have:
                continue
            room -= 1
            seg = store._db.execute("SELECT substr(v.text, p.start_pos + 1, p.end_pos - "
                                    "p.start_pos) FROM main.passages p JOIN main.source_versions "
                                    "v ON v.id=p.version_id WHERE p.id=?", (r[0],)).fetchone()[0]
            store._new("claim_candidates", "INSERT INTO main.claim_candidates (passage_id, text, "
                       "subject, state, claim_id, created) VALUES (?,?,?,'open',NULL,?)",
                       (r[0], seg, _display(seg, r[2]), _now()))
    out = []
    for r in store._db.execute(
            "SELECT c.id, c.text, c.subject, p.source_id, s.title, v.version, v.author_kind, "
            "v.author_name, v.publisher, v.provenance FROM main.claim_candidates c JOIN "
            "main.passages p ON p.id=c.passage_id JOIN main.source_versions v ON "
            "v.id=p.version_id JOIN main.sources s ON s.id=p.source_id WHERE c.state='open' AND "
            "v.state='current' ORDER BY c.id LIMIT ?", (limit,)):
        d = dict(r)
        d["label"] = label(d)
        out.append(d)
    return out


def accept(store, candidate_id: int) -> int:
    """Accept ONE suggestion: it becomes an unverified personal claim, with
    the passage cited as evidence. Returns the claim id."""
    c = store._db.execute("SELECT c.*, p.source_id FROM main.claim_candidates c JOIN "
                          "main.passages p ON p.id=c.passage_id WHERE c.id=?",
                          (candidate_id,)).fetchone()
    if c is None or c["state"] != "open":
        raise BrainError("no open suggestion with that number")
    with _tx(store):
        cid = store.add_claim(c["text"], "imported", by=store.owner, subject=c["subject"] or "")
        cite(store, cid, c["source_id"], c["text"])
        store._db.execute("UPDATE main.claim_candidates SET state='accepted', claim_id=? "
                          "WHERE id=?", (cid, candidate_id))
    return cid


def dismiss(store, candidate_id: int) -> None:
    with _tx(store):
        n = store._db.execute("UPDATE main.claim_candidates SET state='dismissed' WHERE id=? "
                              "AND state='open'", (candidate_id,)).rowcount
        if not n:
            raise BrainError("no open suggestion with that number")


# ------------------------------------------------------------------ search
def search_passages(store, question: str, limit: int = 20) -> List[dict]:
    """Passages of current versions the session may see (its own + shared),
    best match first. Reading material - never an answer by itself."""
    words = sorted(set(terms(question or "")))
    if not words or not _b6(store):
        return []
    ids = set()
    match = " OR ".join('"%s"' % w.replace('"', '') for w in words)
    schemas = ["main"] + (["shared"] if store.attached else [])
    for sch in schemas:
        if _has_fts(store, sch):             # holds current versions only: ranked, limited
            ids |= {r[0] for r in store._db.execute(
                f"SELECT rowid FROM {sch}.passage_fts WHERE passage_fts MATCH ? ORDER BY rank "
                f"LIMIT ?", (match, limit * 4))}
        else:
            for w in words:
                ids |= {r[0] for r in store._db.execute(
                    f"SELECT passage_id FROM {sch}.passage_index WHERE ' '||terms||' ' LIKE ?",
                    (f"% {w} %",))}
    scored = []
    want = set(words)
    for i in range(0, len(ids), 500):
        chunk = list(ids)[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in store._db.execute(
                f"SELECT p.*, x.terms, substr(v.text, p.start_pos + 1, p.end_pos - p.start_pos) "
                f"AS seg, v.version, v.fmt, v.author_kind, v.author_name, "
                f"v.publisher, v.provenance, s.title, s.private, s.added_by FROM passages p "
                f"JOIN passage_index x ON x.passage_id=p.id JOIN source_versions v ON "
                f"v.id=p.version_id JOIN sources s ON s.id=p.source_id WHERE p.id IN ({q}) AND "
                f"v.state='current' AND v.text IS NOT NULL", chunk):
            score = len(want & set(r["terms"].split()))
            scored.append((-score, r["id"], r))
    scored.sort(key=lambda t: (t[0], t[1]))
    out = []
    for _, _, r in scored[:limit]:
        flags = json.loads(r["flags"] or "[]")
        out.append({"passage_id": r["id"], "text": r["seg"],
                    "kind": r["kind"], "heading_path": r["heading_path"],
                    "source_id": r["source_id"], "source": r["title"] or "",
                    "version": r["version"], "label": label(dict(r)), "flags": flags,
                    "scope": "personal" if store._db.execute(
                        "SELECT 1 FROM main.passages WHERE id=?", (r["id"],)).fetchone()
                    else "shared",
                    "can_support": r["kind"] in SUPPORT_KINDS and not flags
                    and r["author_kind"] not in NOT_EVIDENCE})
        if r["fmt"] == "pdf":                  # the page it is on (PDF)
            from .pdf_import import page_of
            st = store._db.execute("SELECT structure FROM source_versions WHERE id=?",
                                   (r["version_id"],)).fetchone()
            pg = page_of(st[0] if st else "[]", r["start_pos"], r["end_pos"])
            out[-1]["page"] = pg["page"] if pg else None
    return out


# ------------------------------------------------------------------ challenges
def challenges(store, fact: dict, viewer_owner: Optional[str]) -> List[dict]:
    """Passages the session may see that state ANOTHER value for the same
    relation / place / qualifier as `fact` (a stored claim fact). Grouped by
    publisher (or, without one, by the material's tag), so a hundred copies
    are one challenge. A group whose sources also state the answer's own
    value is marked internal (the material contradicts itself)."""
    if not _b6(store) or not fact or fact.get("relation") is None:
        return []
    rows = store._db.execute(
        "SELECT pf.value, pf.absent, pf.display, p.id AS pid, p.start_pos, p.end_pos, "
        "v.id AS vid, v.fmt, "
        "substr(v.text, p.start_pos + 1, p.end_pos - p.start_pos) AS seg, "
        "p.source_id, v.text_tag, v.publisher, v.author_kind, v.author_name, "
        "v.provenance, s.title FROM passage_facts pf JOIN passages p ON p.id=pf.passage_id "
        "JOIN source_versions v ON v.id=p.version_id JOIN sources s ON s.id=p.source_id "
        "WHERE pf.relation=? AND pf.place=? AND pf.qualifier=? AND v.state='current' AND "
        "v.text IS NOT NULL AND p.flags='[]' AND v.author_kind NOT IN ('self','ai_generated')",
        (fact["relation"], fact["place"], fact.get("qualifier") or "")).fetchall()
    mine = (fact.get("value"), int(bool(fact.get("absent"))))
    groups: Dict[str, dict] = {}
    agree_groups = set()
    for r in rows:
        g = ("publisher:" + r["publisher"].strip().lower()) if (r["publisher"] or "").strip() \
            else "material:" + r["text_tag"]
        if (r["value"], r["absent"]) == mine:
            agree_groups.add(g)
            continue
        scope = "personal" if store._db.execute("SELECT 1 FROM main.passages WHERE id=?",
                                                (r["pid"],)).fetchone() else "shared"
        d = groups.setdefault(g, {"values": [], "sources": set(), "passages": 0, "examples": [],
                                  "publisher": r["publisher"] or "", "labels": set(),
                                  "scope": "shared", "internal": False})
        val = None if r["absent"] else (r["display"] or r["value"])
        if val not in d["values"]:
            d["values"].append(val)
        d["sources"].add(r["source_id"])
        d["passages"] += 1
        d["labels"].add(label(dict(r)))
        if scope == "personal":
            d["scope"] = "personal"
        if len(d["examples"]) < 3:
            page = None
            if r["fmt"] == "pdf":                  # B6: the page of a PDF passage
                from .pdf_import import page_of
                st = store._db.execute("SELECT structure FROM source_versions WHERE id=?",
                                       (r["vid"],)).fetchone()
                pg = page_of(st[0] if st else "[]", r["start_pos"], r["end_pos"])
                page = pg["page"] if pg else None
            d["examples"].append({"text": r["seg"],
                                  "source": r["title"] or "", "page": page})
    out = []
    for g, d in sorted(groups.items()):
        d["internal"] = g in agree_groups
        d["sources"] = len(d["sources"])
        d["labels"] = sorted(d["labels"])
        d["counts_as"] = "a challenge only - never a confirmation and never a change of category"
        out.append(d)
    return out
