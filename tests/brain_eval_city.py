"""B2 realistic retrieval evaluation, set 2: "Ciudad Coral" (synthetic).

A fictional harbour town - no real habbosk room, ID, person or user - built
only in a temporary database, never in knowledge.db. Unlike set 1 it has
several people using the Brain (Ana = the local user, Luis, Marta and a
moderator), each with private knowledge, a shared library that went through
the full moderator flow, an unreviewed proposal, and questions that need
more than one fact.

Run:  python tests/brain_eval_city.py        (prints the report)
Used by tests/test_brain_eval.py.

Scoring per question kind:
  answer     the expected claim is first AND presented as matching the question
  unknown    nothing is presented as the answer (labelled related results allowed)
  conflict   both sides of the contradiction are in the results
  ambiguous  every reading is offered (top 4) and none is presented as THE answer
  multi      needs two facts chained (B2 doesn't reason - that's B3): both facts
             are retrieved (top 6) and NO single claim is presented as the answer
  compound   asks two things at once: both facts retrieved, neither presented as
             the complete answer (each gives only part)
  privacy    nothing the asker may not see appears anywhere in the results
Plus, over every result of every question: the status shown is the claim's
real status, only verified results are 'established', and anything not
verified is labelled. And a privacy sweep: every claim through claim(),
explain() (evidence, links, events, versions), stats() and search for every
user - nothing private of someone else may appear.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain.store import BrainError  # noqa: E402

DATASET_ID = "SK-BRAIN-EVAL-002"

# key: (text, subject, plan, owner)
#   plan: verified | submitted | awaiting_verification | outdated | rejected |
#         conflict:<group> | shared (moderator flow, verified) | candidate (proposed, unreviewed)
CLAIMS: Dict[str, tuple] = {
    # rooms and permanent IDs - similar names on purpose
    "puerto_id":   ("El Puerto Coral tiene el ID de sala 2104551.", "Puerto Coral", "verified", "ana"),
    "faro_norte_id": ("The Faro del Norte has room ID 2104552.", "Faro del Norte", "verified", "ana"),
    "faro_sur_id": ("El Faro del Sur tiene el ID 2104553.", "Faro del Sur", "submitted", "ana"),
    "central_id":  ("The Mercado Central has room ID 2104560.", "Mercado Central", "verified", "ana"),
    "noct_id":     ("The Mercado Nocturno has room ID 2104561.", "Mercado Nocturno",
                    "awaiting_verification", "ana"),
    "cueva_id":    ("La Cueva Marina tiene el ID de sala 2104570.", "Cueva Marina", "submitted", "ana"),
    "suite_id":    ("The Hotel Coral Suite has room ID 2104581.", "Hotel Coral Suite", "submitted",
                    "ana"),
    "hotel_id":    ("The Hotel Coral has room ID 2104580.", "Hotel Coral", "shared", "mod"),
    # connections and directions
    "puerto_norte": ("La salida norte del Puerto Coral lleva al Faro del Norte.", "Puerto Coral",
                     "verified", "ana"),
    "central_east": ("From the Mercado Central the east stairs lead to the Torre del Reloj.",
                     "Mercado Central", "submitted", "ana"),
    "cueva_access": ("The Cueva Marina can only be reached from the Faro del Sur.", "Cueva Marina",
                     "verified", "ana"),
    "hotel_west":  ("The west door of the Hotel Coral connects to the Mercado Nocturno.",
                    "Hotel Coral", "submitted", "ana"),
    # people and responsibilities - Rafa / Rafael on purpose
    "irene":       ("Capitana Irene runs the Puerto Coral.", "Puerto Coral", "verified", "ana"),
    "rafa":        ("Rafa is the caretaker of the Faro del Norte.", "Faro del Norte", "verified", "ana"),
    "rafael":      ("Rafael is the caretaker of the Faro del Sur.", "Faro del Sur", "submitted", "ana"),
    "tito":        ("El encargado del Mercado Central es Don Tito.", "Mercado Central", "verified",
                    "ana"),
    # rules and exceptions
    "trade_all":   ("Trading is allowed in every market.", "trading", "verified", "ana"),
    "trade_noct":  ("Trading is not allowed in the Mercado Nocturno after midnight.",
                    "Mercado Nocturno", "verified", "ana"),
    "pets_hotel":  ("Pets are not allowed in the Hotel Coral.", "Hotel Coral", "verified", "ana"),
    "pets_suite":  ("Pets are allowed in the Hotel Coral Suite.", "Hotel Coral Suite", "submitted",
                    "ana"),
    "torre_hours": ("The Torre del Reloj opens at 8 and closes at 20.", "Torre del Reloj",
                    "verified", "ana"),
    # quests and prices
    "quest_norte": ("La misión del Faro del Norte da 30 monedas.", "Faro del Norte", "verified",
                    "ana"),
    "quest_sur":   ("La misión del Faro del Sur da 50 monedas.", "Faro del Sur", "submitted", "ana"),
    "hotel_price": ("A room in the Hotel Coral costs 25 coins per night.", "Hotel Coral", "verified",
                    "ana"),
    "taxi_cueva":  ("The taxi to the Cueva Marina costs 5 coins.", "Cueva Marina", "shared", "mod"),
    # contradictions
    "helmet":      ("The Cueva Marina quest requires a diving helmet.", "Cueva Marina", "conflict:a",
                    "ana"),
    "noitem":      ("The Cueva Marina quest does not require any item.", "Cueva Marina", "conflict:a",
                    "ana"),
    "noct_22":     ("El Mercado Nocturno abre a las 22.", "Mercado Nocturno", "conflict:b", "ana"),
    "noct_21":     ("The Mercado Nocturno opens at 21.", "Mercado Nocturno", "conflict:b", "ana"),
    # outdated / rejected
    "puerto_oldid": ("The Puerto Coral has room ID 2104550.", "Puerto Coral", "outdated", "ana"),
    "tito_noct":   ("Don Tito runs the Mercado Nocturno.", "Mercado Nocturno", "rejected", "ana"),
    # other people's private knowledge, and an unreviewed proposal
    "luis_stash":  ("Luis's hidden stash is in the Cueva Marina behind rock 7.", "Cueva Marina",
                    "submitted", "luis"),
    "luis_pass":   ("Luis's trade password is coral77.", "trading", "submitted", "luis"),
    "luis_faro":   ("The Faro del Norte has room ID 9990001.", "Faro del Norte", "submitted", "luis"),
    "marta_meet":  ("Marta meets Irene at the Puerto Coral at 9pm.", "Puerto Coral", "submitted",
                    "marta"),
    "marta_torre": ("The Torre del Reloj has room ID 2104590.", "Torre del Reloj", "candidate",
                    "marta"),
    "ana_locker":  ("My locker code at the Hotel Coral is 8812.", "Hotel Coral", "submitted", "ana"),
}
# words only one person's private claims contain: must never reach anyone else
SECRETS = {"luis": ["coral77", "rock 7", "9990001", "stash"],
           "marta": ["9pm", "2104590"], "ana": ["8812", "locker"]}
CANDIDATE_WORDS = {"marta": ["2104590"]}     # in Marta's unreviewed proposal

Q = []          # (question, kind, expected, asker)
def q(text, kind, expect=(), asker="ana"):  # noqa: E302
    Q.append((text, kind, list(expect), asker))


# -- answers ---------------------------------------------------------------------
q("¿Cuál es el ID del Faro del Sur?", "answer", ["faro_sur_id"])
q("Faro del Norte room ID", "answer", ["faro_norte_id"])
q("What is the ID of the Mercado Nocturno?", "answer", ["noct_id"])
q("ID del Mercado Central", "answer", ["central_id"])
q("Hotel Coral Suite room number", "answer", ["suite_id"])
q("What is the room ID of the Hotel Coral?", "answer", ["hotel_id"])
q("¿Cuál es el ID del Puerto Coral?", "answer", ["puerto_id"])
q("¿A dónde lleva la salida norte del Puerto Coral?", "answer", ["puerto_norte"])
q("How do I reach the Cueva Marina?", "answer", ["cueva_access"])
q("Where do the east stairs of the Mercado Central lead?", "answer", ["central_east"])
q("Who runs the Puerto Coral?", "answer", ["irene"])
q("Who is the caretaker of the Faro del Sur?", "answer", ["rafael"])
q("Who is the caretaker of the Faro del Norte?", "answer", ["rafa"])
q("¿Quién es el encargado del Mercado Central?", "answer", ["tito"])
q("Are pets allowed in the Hotel Coral?", "answer", ["pets_hotel"])
q("Can I bring my pet to the Hotel Coral Suite?", "answer", ["pets_suite"])
q("When does the Torre del Reloj close?", "answer", ["torre_hours"])
q("¿Cuántas monedas da la misión del Faro del Norte?", "answer", ["quest_norte"])
q("How much does a room in the Hotel Coral cost?", "answer", ["hotel_price"])
q("Is trading allowed in the Mercado Nocturno after midnight?", "answer", ["trade_noct"])
q("How much is the taxi to the Cueva Marina?", "answer", ["taxi_cueva"])
q("What is the room ID of the Hotel Coral?", "answer", ["hotel_id"], "luis")   # shared: everyone
# -- contradictions -------------------------------------------------------------
q("Does the Cueva Marina quest require a diving helmet?", "conflict", ["helmet", "noitem"])
q("¿A qué hora abre el Mercado Nocturno?", "conflict", ["noct_22", "noct_21"])
# -- unknowns (and unsupported conclusions) ---------------------------------------
q("What is the room ID of the Torre del Reloj?", "unknown")          # only Marta's proposal
q("Who is the caretaker of the Hotel Coral?", "unknown")
q("Where does the south exit of the Puerto Coral lead?", "unknown")
q("¿Cuántas monedas da la misión del Mercado Central?", "unknown")
q("Who runs the Mercado Nocturno?", "unknown")                       # only a rejected claim
q("Is the Faro del Norte open at night?", "unknown")                 # nothing says so
q("What is the ID of the Faro del Este?", "unknown")                 # similar, nonexistent
q("How much does the taxi to the Faro del Sur cost?", "unknown")     # only the Cueva taxi known
q("Who is the caretaker of the Cueva Marina?", "unknown")
# -- multi-fact (two facts chained) ---------------------------------------------
q("Who is the caretaker of the room the north exit of the Puerto Coral leads to?", "multi",
  ["puerto_norte", "rafa"])
q("What is the room ID of the room where Don Tito is the encargado?", "multi",
  ["tito", "central_id"])
q("How many coins does the quest give in the room Rafael looks after?", "multi",
  ["rafael", "quest_sur"])
# -- compound (two requirements) --------------------------------------------------
q("What is the room ID and who is the caretaker of the Faro del Norte?", "compound",
  ["faro_norte_id", "rafa"])
q("¿Cuál es el ID del Mercado Central y quién es su encargado?", "compound",
  ["central_id", "tito"])
# -- ambiguous ------------------------------------------------------------------
q("What is the room ID of the Faro?", "ambiguous", ["faro_norte_id", "faro_sur_id"])
q("Who is the caretaker of the Faro?", "ambiguous", ["rafa", "rafael"])
q("ID of the Mercado", "ambiguous", ["central_id", "noct_id"])
# -- privacy ----------------------------------------------------------------------
q("Faro del Norte room ID", "privacy", [], "luis")
q("Where is Luis's hidden stash?", "privacy", [], "marta")
q("Torre del Reloj room ID", "privacy", [], "mod")          # proposals stay out of search
q("trade password coral", "privacy", [], "ana")
q("Marta Irene Puerto Coral 9pm", "privacy", [], "luis")
q("locker code Hotel Coral", "privacy", [], "luis")
QUESTIONS = Q


def build(path: str):
    b = Brain(path, "", "")
    st = b.store
    users = {"ana": b.local_user, "luis": st.add_contributor("Luis (eval)", "external_user"),
             "marta": st.add_contributor("Marta (eval)", "external_user"),
             "mod": st.add_contributor("Moderator (eval)", "moderator")}
    srcs = {u: b.add_source("screenshot", title=f"{DATASET_ID} {u}",
                            reference=f"eval://{DATASET_ID}/{u}", by=who)
            for u, who in users.items()}
    ids, conflicts = {}, {}
    for key, (text, subject, plan, owner) in CLAIMS.items():
        who = users[owner]
        cid = b.learn(text, "user", subject, by=who)
        ids[key] = cid
        if plan in ("verified", "shared"):
            b.add_evidence(cid, "direct", srcs[owner], key, by=who)
            b.review(cid, "verified", reviewer=who)
            if plan == "shared":
                b.propose_share(cid, by=who)
                b.review(cid, "verified", reviewer=users["mod"])
                b.approve_share(cid, users["mod"])
        elif plan == "candidate":
            b.propose_share(cid, by=who)
        elif plan in ("awaiting_verification", "outdated", "rejected"):
            b.review(cid, plan, reviewer=who)
        elif plan.startswith("conflict:"):
            conflicts.setdefault(plan, []).append(cid)
    for a, c in conflicts.values():
        b.link(a, c, "contradicts")
    return b, ids, users


def _may_see(key, asker):
    plan, owner = CLAIMS[key][2], CLAIMS[key][3]
    return owner == asker or plan == "shared"


def evaluate(use_fts=None) -> dict:
    d = tempfile.mkdtemp(prefix="sk_brain_eval_city_")
    b, ids, users = build(os.path.join(d, "eval.db"))
    key_of = {str(v): k for k, v in ids.items()}
    rows, status_errors, sweep_errors = [], [], []
    try:
        for text, kind, expect, asker in QUESTIONS:
            hits = [h for h in b.search(text, viewer=users[asker], use_fts=use_fts)
                    if h.module == "library"]
            got = [key_of[h.record_id] for h in hits]
            match = [key_of[h.record_id] for h in hits if h.detail["why"]["matches_question"]]
            for h in hits:                       # status shown == real status, labelled
                real = b.store._claim(int(h.record_id))["status"]
                if h.status != real or h.established != (real == "verified") or \
                        (real != "verified" and not h.display().startswith("[")):
                    status_errors.append((text, key_of[h.record_id], h.status, real))
            leaked = [k for k in got if not _may_see(k, asker)]
            if kind == "answer":
                ok = bool(got) and got[0] in expect and got[0] in match
            elif kind == "unknown":
                ok = not match
            elif kind == "conflict":
                ok = all(k in got for k in expect)
            elif kind == "ambiguous":
                ok = all(k in got[:4] for k in expect) and not match
            elif kind in ("multi", "compound"):
                ok = all(k in got[:6] for k in expect) and not match
            else:
                ok = not leaked
            if leaked:
                ok = False
            rows.append({"q": text, "kind": kind, "asker": asker, "expect": expect,
                         "got": got[:6], "match": match, "ok": ok, "leaked": leaked,
                         "retrieved": all(k in got[:6] for k in expect)})
        # privacy sweep: every claim, every read path, every user
        for asker, who in users.items():
            foreign = [w for u, ws in SECRETS.items() if u != asker for w in ws]
            # moderators may read proposals (to review them) - but never find them by search
            reviewable = [w for u, ws in CANDIDATE_WORDS.items() if u != asker for w in ws] \
                if asker == "mod" else []
            seen = [repr(b.claim(cid, viewer=who)) + repr(b.explain(cid, viewer=who))
                    for cid in ids.values()]
            seen = [x for x in seen if not any(w in x for w in reviewable)]
            seen.append(repr(b.stats(viewer=who)))
            for cid in ids.values():             # error messages for others' claims
                for act in (lambda: b.add_evidence(cid, "assertion", by=who),
                            lambda: b.review(cid, "rejected", reviewer=who),
                            lambda: b.link(cid, ids["trade_all"], "supports", by=who),
                            lambda: b.propose_correction(cid, "x", by=who)):
                    try:
                        act()
                    except BrainError as exc:
                        seen.append(str(exc))
            for w in ("Cueva Marina stash rock", "trade password", "Puerto Coral Irene 9pm",
                      "Torre del Reloj ID", "locker code Hotel Coral", "Faro del Norte ID"):
                seen += [h.text for h in b.search(w, viewer=who)]
            hits = []
            for w in ("Cueva Marina stash rock", "trade password", "Puerto Coral Irene 9pm",
                      "Torre del Reloj ID", "locker code Hotel Coral", "Faro del Norte ID"):
                hits += [h.text for h in b.search(w, viewer=who)]
            sweep_errors += [(asker, "search", w) for w in foreign
                             if w.lower() in "\n".join(hits).lower()]
            blob = "\n".join(seen).lower()
            sweep_errors += [(asker, w) for w in foreign if w.lower() in blob]
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)
    summary = {}
    for kind in ("answer", "unknown", "conflict", "ambiguous", "multi", "compound", "privacy"):
        rs = [r for r in rows if r["kind"] == kind]
        summary[kind] = (sum(r["ok"] for r in rs), len(rs))
    return {"rows": rows, "summary": summary, "status_errors": status_errors,
            "sweep_errors": sweep_errors}


def report(res: dict) -> str:
    out = [f"Dataset {DATASET_ID}: {len(CLAIMS)} claims, 4 users, {len(res['rows'])} questions"]
    for kind, (ok, n) in res["summary"].items():
        out.append(f"  {kind:10} {ok}/{n}")
    multi = [r for r in res["rows"] if r["kind"] in ("multi", "compound")]
    out.append(f"  multi/compound facts retrieved (regardless of labels): "
               f"{sum(r['retrieved'] for r in multi)}/{len(multi)}")
    out.append(f"  status shown wrongly: {len(res['status_errors'])}   "
               f"privacy sweep leaks: {len(res['sweep_errors'])}")
    out.append("Failures:")
    for r in res["rows"]:
        if not r["ok"]:
            out.append(f"  [{r['kind']}] {r['q']!r} (asked by {r['asker']})\n"
                       f"      expected {r['expect']}  got {r['got']}  presented as answer: "
                       f"{r['match']}" + (f"  LEAKED {r['leaked']}" if r["leaked"] else ""))
    for e in res["status_errors"] + res["sweep_errors"]:
        out.append(f"  ERROR {e}")
    return "\n".join(out)


if __name__ == "__main__":
    res = evaluate()
    print(report(res))
    fb = evaluate(use_fts=False)
    print("FTS5 and fallback identical:",
          [(r["got"], r["match"]) for r in res["rows"]] == [(r["got"], r["match"]) for r in fb["rows"]])
