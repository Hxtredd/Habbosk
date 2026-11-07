"""Automatic checks for the temporary SK Brain test bench (no interface).

The scenario checks run on a THROWAWAY database in a temporary folder, so
nothing is ever added to the owner's real knowledge.db. The read-only check
uses the real quiz memory / World Atlas files: it only reads them and
compares their checksums before and after.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from typing import List, Optional, Tuple

from ..brain import Brain
from ..brain.store import BrainError

Result = Tuple[str, bool, str]          # (check, passed, detail)


def _hash(path: str) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def scenario_checks() -> List[Result]:
    out: List[Result] = []
    folder = tempfile.mkdtemp(prefix="sk_brain_check_")
    b = Brain(os.path.join(folder, "knowledge.db"), "", "")
    try:
        src = b.add_source("guide_page", title="Guía de prueba", reference="test://guia",
                           raw_text="El Spa está en Avenida Espumia. El taxi es gratis.")
        sec = b.add_section(src, "El Spa está en Avenida Espumia. El taxi es gratis.", "Guía")
        spa = b.learn("El Spa está en Avenida Espumia.", "imported", "Spa", "lugares", sec)
        b.add_evidence(spa, "direct", src, "párrafo 1")

        hits = b.search("¿Dónde está el Spa?", modules=["library"])
        out.append(("An entered claim is retrieved",
                    bool(hits) and hits[0].record_id == str(spa),
                    f"{len(hits)} result(s); first: {hits[0].text if hits else '-'}"))
        ev = b.explain(spa)["evidence"]
        out.append(("Its source is the right one",
                    bool(ev) and ev[0]["title"] == "Guía de prueba",
                    f"source: {ev[0]['title'] if ev else '-'} ({ev[0]['reference'] if ev else ''})"))
        out.append(("Unverified is labelled, never shown as fact",
                    bool(hits) and not hits[0].established and hits[0].display().startswith("["),
                    hits[0].display() if hits else "-"))

        a = b.learn("El taxi cuesta $5.", "user", "taxi")
        c = b.learn("El taxi es gratis.", "imported", "taxi", section_id=sec)
        b.link(c, a, "contradicts")
        th = {h.record_id: h for h in b.search("taxi", modules=["library"])}
        out.append(("Conflicting claims stay distinguishable",
                    th.get(str(a)) is not None and th[str(a)].status == "conflicting"
                    and th.get(str(c)) is not None and th[str(c)].status == "conflicting",
                    " | ".join(h.display() for h in th.values())))

        none = b.search("helicóptero submarino volcán", modules=["library"])
        out.append(("A question with nothing matching returns nothing",
                    none == [], f"{len(none)} result(s)"))

        other = b.store.add_contributor("Test contributor", "external_user")
        seen = b.search("Spa Avenida Espumia", modules=["library"], viewer=other)
        out.append(("Personal knowledge is isolated from other contributors",
                    seen == [] and b.claim(spa, viewer=other) is None,
                    f"another contributor sees {len(seen)} of your claims"))
        try:
            b.add_evidence(spa, "assertion", by=other)
            out.append(("Others cannot add to your claims", False, "it was allowed"))
        except BrainError as exc:
            out.append(("Others cannot add to your claims", True,
                        f"refused - for them your claim doesn't exist ({exc})"))
        imp = b.learn("El banco abre a las 9.", "imported", "Banco", by="importer")
        b.add_evidence(imp, "direct", src, "párrafo 2", by="importer")
        try:
            b.review(imp, "verified", reviewer="importer")
            out.append(("No automatic verification", False, "the importer verified its own claim"))
        except BrainError as exc:
            out.append(("No automatic verification", "automatic" in str(exc), str(exc)))
    finally:
        b.close()
        shutil.rmtree(folder, ignore_errors=True)
    return out


def readonly_check(brain: Brain) -> List[Result]:
    """Search the real quiz memory and World Atlas, then make sure their
    files did not change."""
    paths = {s.module: s.ro.path for s in brain.sources}
    before = {m: _hash(p) for m, p in paths.items()}
    for q in ("Leo", "Gran Casino", "Avenida Alameda", "capital"):
        brain.search(q)
    out = []
    for m, p in paths.items():
        h = _hash(p)
        if before[m] is None:
            out.append((f"{m} stays read-only", True, "file not found - nothing to read (not created)"
                        if not os.path.exists(p or "") else "unreadable"))
        else:
            out.append((f"{m} stays read-only", h == before[m],
                        "unchanged after searching" if h == before[m] else "FILE CHANGED"))
    return out
