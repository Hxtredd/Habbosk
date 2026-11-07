"""B2 realistic retrieval evaluation (synthetic, separate from user data).

A small fictional town ("Villa Brisa": no real habbosk room, ID or person)
built in a temporary database. It has the kinds of knowledge the Brain will
hold - room names and permanent IDs, connections and directions, people and
responsibilities, rules and exceptions, verified / unverified / outdated /
rejected claims, contradictions, similar names, a second user's private
claims - and questions of each kind, including unknowns and ambiguous ones.

Run:  python tests/brain_eval.py        (prints the report)
Used by tests/test_brain_eval.py (hard guarantees + no-regression floors).

Scoring (per question kind):
  answer        the expected claim is the FIRST result and the Brain says it
                matches the question (strict); also reported: in the top 3
  unknown       nothing in the results is presented as matching the question
                (related results may still be listed, labelled)
  conflict      both sides of the contradiction are in the results
  ambiguous     every acceptable reading is in the top results
  privacy       nothing the asker may not see appears, in any position
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from puzzle_assistant.brain import Brain  # noqa: E402

DATASET_ID = "SK-BRAIN-EVAL-001"

# key: (text, subject, plan, owner)  plan = final status; owner "ana" (local) or "luis"
CLAIMS: Dict[str, tuple] = {
    # rooms and permanent IDs (two similar Jardín names, two similar Sala Azul names)
    "plaza_id":    ("The Plaza Mayor has room ID 100201.", "Plaza Mayor", "verified", "ana"),
    "rosas_id":    ("El Jardín de las Rosas tiene el ID de sala 100202.", "Jardín de las Rosas",
                    "verified", "ana"),
    "luna_id":     ("The Jardín de la Luna has room ID 100203.", "Jardín de la Luna",
                    "submitted", "ana"),
    "azul_id":     ("The Sala Azul has room ID 100210.", "Sala Azul", "verified", "ana"),
    "azul2_id":    ("The Sala Azul 2 has room ID 100211.", "Sala Azul 2", "submitted", "ana"),
    "biblio_id":   ("The Biblioteca Antigua has room ID 100220.", "Biblioteca Antigua",
                    "awaiting_verification", "ana"),
    "bosque_id":   ("The Bosque Oscuro has room ID 100230.", "Bosque Oscuro", "verified", "ana"),
    "cement_noid": ("The Cementerio Viejo has no permanent room ID; it is only reached from the "
                    "Bosque Oscuro.", "Cementerio Viejo", "verified", "ana"),
    # connections and directions
    "plaza_north": ("The north door of the Plaza Mayor leads to the Jardín de las Rosas.",
                    "Plaza Mayor", "verified", "ana"),
    "plaza_east":  ("The east exit of the Plaza Mayor leads to the Sala Azul.", "Plaza Mayor",
                    "submitted", "ana"),
    "bosque_west": ("From the Bosque Oscuro the west path leads to the Cementerio Viejo.",
                    "Bosque Oscuro", "verified", "ana"),
    "luna_arch":   ("The Jardín de la Luna connects to the Biblioteca Antigua through the archway.",
                    "Jardín de la Luna", "submitted", "ana"),
    # people and responsibilities
    "marta_rosas": ("Marta is the caretaker of the Jardín de las Rosas.", "Jardín de las Rosas",
                    "verified", "ana"),
    "tomas_biblio": ("Tomás maintains the Biblioteca Antigua.", "Biblioteca Antigua",
                     "submitted", "ana"),
    "leo_shop":    ("Leo runs the shop in the Plaza Mayor.", "Plaza Mayor", "verified", "ana"),
    "azul_nocare": ("The Sala Azul has no caretaker.", "Sala Azul", "verified", "ana"),
    "rafa_azul2":  ("El encargado de la Sala Azul 2 es Rafa.", "Sala Azul 2", "submitted", "ana"),
    # rules and exceptions
    "taxi_all":    ("The taxi works in every room.", "taxi", "verified", "ana"),
    "taxi_cement": ("The taxi does not work in the Cementerio Viejo.", "Cementerio Viejo",
                    "verified", "ana"),
    "pets_rosas":  ("Pets are allowed in the Jardín de las Rosas.", "Jardín de las Rosas",
                    "submitted", "ana"),
    "pets_biblio": ("Pets are not allowed in the Biblioteca Antigua.", "Biblioteca Antigua",
                    "verified", "ana"),
    "biblio_close": ("The Biblioteca Antigua closes at midnight.", "Biblioteca Antigua",
                     "verified", "ana"),
    "fuente_luna": ("La Fuente de los Deseos está en el Jardín de la Luna.", "Fuente de los Deseos",
                    "submitted", "ana"),
    # contradictions (linked, both 'conflicting')
    "fountain_ok": ("The fountain in the Plaza Mayor is working.", "Plaza Mayor", "conflict:a",
                    "ana"),
    "fountain_bad": ("The fountain in the Plaza Mayor is broken.", "Plaza Mayor", "conflict:a",
                     "ana"),
    "quest_50":    ("The Sala Azul quest costs 50 coins.", "Sala Azul", "conflict:b", "ana"),
    "quest_free":  ("The Sala Azul quest is free.", "Sala Azul", "conflict:b", "ana"),
    # outdated / rejected
    "plaza_oldid": ("The Plaza Mayor has room ID 100200.", "Plaza Mayor", "outdated", "ana"),
    "marta_luna":  ("Marta is the caretaker of the Jardín de la Luna.", "Jardín de la Luna",
                    "rejected", "ana"),
    # another user's private knowledge (never visible to Ana, and vice versa)
    "luis_hideout": ("Luis's secret hideout is the Sala Azul 2.", "Sala Azul 2", "submitted",
                     "luis"),
    "luis_biblio": ("The Biblioteca Antigua has room ID 999999.", "Biblioteca Antigua",
                    "submitted", "luis"),
}

# (question, kind, expected keys, asker)
QUESTIONS: List[tuple] = [
    # room IDs
    ("What is the room ID of the Plaza Mayor?", "answer", ["plaza_id"], "ana"),
    ("¿Cuál es el ID de la sala del Jardín de las Rosas?", "answer", ["rosas_id"], "ana"),
    ("ID of Jardín de la Luna", "answer", ["luna_id"], "ana"),
    ("room number of the Sala Azul", "answer", ["azul_id"], "ana"),
    ("What is the ID of Sala Azul 2?", "answer", ["azul2_id"], "ana"),
    ("Biblioteca Antigua room id", "answer", ["biblio_id"], "ana"),
    ("numero de sala del bosque oscuro", "answer", ["bosque_id"], "ana"),
    ("What is the room ID of the Cementerio Viejo?", "answer", ["cement_noid"], "ana"),
    # connections and directions
    ("Where does the north door of the Plaza Mayor lead?", "answer", ["plaza_north"], "ana"),
    ("How do I get to the Cementerio Viejo?", "answer", ["bosque_west"], "ana"),
    ("Which exit of the Plaza Mayor goes to the Sala Azul?", "answer", ["plaza_east"], "ana"),
    ("What connects to the Biblioteca Antigua?", "answer", ["luna_arch"], "ana"),
    # people
    ("Who takes care of the Jardín de las Rosas?", "answer", ["marta_rosas"], "ana"),
    ("Who is the caretaker of the Biblioteca Antigua?", "answer", ["tomas_biblio"], "ana"),
    ("Who runs the shop in the Plaza Mayor?", "answer", ["leo_shop"], "ana"),
    ("¿Quién es el encargado de la Sala Azul 2?", "answer", ["rafa_azul2"], "ana"),
    ("Who is the caretaker of the Sala Azul?", "answer", ["azul_nocare"], "ana"),
    # rules and exceptions
    ("Does the taxi work in the Cementerio Viejo?", "answer", ["taxi_cement"], "ana"),
    ("Does the taxi work everywhere?", "answer", ["taxi_all"], "ana"),
    ("Are pets allowed in the Biblioteca Antigua?", "answer", ["pets_biblio"], "ana"),
    ("Can I bring pets to the Jardín de las Rosas?", "answer", ["pets_rosas"], "ana"),
    ("When does the Biblioteca Antigua close?", "answer", ["biblio_close"], "ana"),
    ("¿Dónde está la Fuente de los Deseos?", "answer", ["fuente_luna"], "ana"),
    # contradictions: both sides must be shown
    ("Is the fountain in the Plaza Mayor working?", "conflict", ["fountain_ok", "fountain_bad"],
     "ana"),
    ("How much does the Sala Azul quest cost?", "conflict", ["quest_50", "quest_free"], "ana"),
    # unknowns: nothing may be presented as the answer
    ("Who is the caretaker of the Bosque Oscuro?", "unknown", [], "ana"),
    ("What is the room ID of the Jardín del Sol?", "unknown", [], "ana"),
    ("Who runs the shop in the Sala Azul?", "unknown", [], "ana"),
    ("When does the Plaza Mayor close?", "unknown", [], "ana"),
    ("Who is the caretaker of the Jardín de la Luna?", "unknown", [], "ana"),   # only rejected
    ("Where does the south door of the Plaza Mayor lead?", "unknown", [], "ana"),
    ("¿Quién mantiene el Cementerio Viejo?", "unknown", [], "ana"),
    # ambiguous: every reading should be offered
    ("What is the room ID of the Jardín?", "ambiguous", ["rosas_id", "luna_id"], "ana"),
    ("Who is the caretaker?", "ambiguous", ["marta_rosas", "tomas_biblio", "rafa_azul2"], "ana"),
    # privacy (another user's private claims must never appear)
    ("Biblioteca Antigua room id", "privacy", ["luis_biblio", "luis_hideout"], "ana"),
    ("Where is Luis's secret hideout?", "privacy", ["luis_hideout", "luis_biblio"], "ana"),
    ("Biblioteca Antigua room id", "privacy", [k for k in CLAIMS if CLAIMS[k][3] == "ana"],
     "luis"),
]


# Held-out questions: written AFTER the first run of QUESTIONS and BEFORE any
# retrieval change made in the B2 validation, and never used to tune it -
# a check that changes did not just fit the questions above.
HELDOUT: List[tuple] = [
    ("Plaza Mayor ID?", "answer", ["plaza_id"], "ana"),
    ("¿Qué número tiene el Jardín de la Luna?", "answer", ["luna_id"], "ana"),
    ("Tell me the code of the Bosque Oscuro room", "answer", ["bosque_id"], "ana"),
    ("Sala Azul 2 room number", "answer", ["azul2_id"], "ana"),
    ("¿Cuál es el código de la Biblioteca Antigua?", "answer", ["biblio_id"], "ana"),
    ("Where does the east exit of the Plaza Mayor go?", "answer", ["plaza_east"], "ana"),
    ("¿Cómo llego al Cementerio Viejo?", "answer", ["bosque_west"], "ana"),
    ("Where does the west path of the Bosque Oscuro lead?", "answer", ["bosque_west"], "ana"),
    ("Who looks after the Jardín de las Rosas?", "answer", ["marta_rosas"], "ana"),
    ("¿Quién cuida la Biblioteca Antigua?", "answer", ["tomas_biblio"], "ana"),
    ("Who is the owner of the shop in the Plaza Mayor?", "answer", ["leo_shop"], "ana"),
    ("Can I take the taxi to the Cementerio Viejo?", "answer", ["taxi_cement"], "ana"),
    ("Is the taxi available in every room?", "answer", ["taxi_all"], "ana"),
    ("¿Se permiten mascotas en la Biblioteca Antigua?", "answer", ["pets_biblio"], "ana"),
    ("Is the fountain in the Plaza Mayor broken?", "conflict", ["fountain_ok", "fountain_bad"],
     "ana"),
    ("¿Cuánto cuesta la misión de la Sala Azul?", "conflict", ["quest_50", "quest_free"], "ana"),
    ("Who is the caretaker of the Plaza Mayor?", "unknown", [], "ana"),
    ("What is the room ID of the Sala Roja?", "unknown", [], "ana"),
    ("Where does the west door of the Plaza Mayor lead?", "unknown", [], "ana"),
    ("Where does the north path of the Bosque Oscuro lead?", "unknown", [], "ana"),
    ("Does the taxi work in the Jardín del Sol?", "unknown", [], "ana"),
    ("When does the Sala Azul open?", "unknown", [], "ana"),
    ("Room ID of the Sala Azul", "answer", ["azul_id"], "ana"),
    ("Who is Luis's hideout?", "privacy", ["luis_hideout", "luis_biblio"], "ana"),
    ("Sala Azul 2 hideout", "privacy", ["luis_hideout"], "ana"),
    ("Plaza Mayor room id", "privacy", [k for k in CLAIMS if CLAIMS[k][3] == "ana"], "luis"),
]


def build(path: str):
    """The dataset in a new database at `path`. Returns (brain, ids, users)."""
    b = Brain(path, "", "")
    ana = b.local_user
    luis = b.store.add_contributor("Luis (eval)", "external_user")
    users = {"ana": ana, "luis": luis}
    src = b.add_source("document", title=DATASET_ID, reference="eval://" + DATASET_ID)
    ids: Dict[str, int] = {}
    conflicts: Dict[str, List[int]] = {}
    for key, (text, subject, plan, owner) in CLAIMS.items():
        who = users[owner]
        cid = b.learn(text, "user", subject, by=who)
        ids[key] = cid
        if plan == "verified":
            b.add_evidence(cid, "direct", src, key, by=who)
            b.review(cid, "verified", reviewer=who)
        elif plan in ("awaiting_verification", "outdated", "rejected"):
            b.review(cid, plan, reviewer=who)
        elif plan.startswith("conflict:"):
            conflicts.setdefault(plan, []).append(cid)
    for a, c in conflicts.values():
        b.link(a, c, "contradicts")
    return b, ids, users


def evaluate(use_fts=None, questions=None) -> dict:
    d = tempfile.mkdtemp(prefix="sk_brain_eval_")
    b, ids, users = build(os.path.join(d, "eval.db"))
    key_of = {str(v): k for k, v in ids.items()}
    rows = []
    try:
        for q, kind, expect, asker in (QUESTIONS if questions is None else questions):
            hits = b.search(q, viewer=users[asker], use_fts=use_fts)
            got = [key_of.get(h.record_id, h.record_id) for h in hits if h.module == "library"]
            match = [key_of.get(h.record_id) for h in hits
                     if h.detail.get("why", {}).get("matches_question")]
            if kind == "answer":
                ok = bool(got) and got[0] in expect and got[0] in match
                ok3 = any(k in got[:3] for k in expect)
            elif kind == "unknown":
                ok = ok3 = not match
            elif kind == "conflict":
                ok = ok3 = all(k in got for k in expect)
            elif kind == "ambiguous":
                ok = ok3 = all(k in got[:max(3, len(expect))] for k in expect)
            else:   # privacy: none of `expect` may appear anywhere
                ok = ok3 = not any(k in got for k in expect)
            rows.append({"q": q, "kind": kind, "asker": asker, "expect": expect, "got": got[:5],
                         "match": match, "ok": ok, "ok3": ok3,
                         "statuses": [h.status for h in hits[:5]]})
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)
    summary = {}
    for kind in ("answer", "unknown", "conflict", "ambiguous", "privacy"):
        rs = [r for r in rows if r["kind"] == kind]
        summary[kind] = (sum(r["ok"] for r in rs), sum(r["ok3"] for r in rs), len(rs))
    return {"rows": rows, "summary": summary}


def report(res: dict, title: str = "") -> str:
    out = [f"Dataset {DATASET_ID}: {len(CLAIMS)} claims, {len(res['rows'])} questions {title}"]
    for kind, (ok, ok3, n) in res["summary"].items():
        extra = f"   (expected claim in top 3: {ok3}/{n})" if kind == "answer" else ""
        out.append(f"  {kind:10} {ok}/{n}{extra}")
    out.append("Failures:")
    for r in res["rows"]:
        if not r["ok"]:
            out.append(f"  [{r['kind']}] {r['q']!r} (asked by {r['asker']})\n"
                       f"      expected {r['expect'][:4]}  got {r['got']}  "
                       f"presented as answer: {r['match']}")
    return "\n".join(out)


if __name__ == "__main__":
    for title, qs in (("(main)", QUESTIONS), ("(held-out)", HELDOUT)):
        res = evaluate(questions=qs)
        print(report(res, title))
        fb = evaluate(use_fts=False, questions=qs)
        print("FTS5 and fallback give identical results:",
              [r["got"] for r in res["rows"]] == [r["got"] for r in fb["rows"]], "\n")
