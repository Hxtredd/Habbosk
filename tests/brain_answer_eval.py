"""B4 answer-category evaluation (synthetic, separate from user data).

"Puerto Nube": a small fictional place with every situation an answer can
be in - verified facts, unverified claims whose source quote does or does
not state the same fact (wrong value, negation, hedge, condition, past,
another place, run-on, context, empty quote, a quote that needs the claim's
subject, someone's own statement, an assertion, no evidence), a
contradiction, an outdated-only fact, an ambiguous name, a rejected claim,
shared knowledge, another user's private knowledge - plus everyday
personal-assistant questions SK cannot answer yet.

Every question has its EXPECTED category, reason and value, written from
what the data supports (ground truth), not from what the code returns.
The Villa Brisa / Ciudad Coral sets are scored by their kinds.

Run:  python tests/brain_answer_eval.py        (prints the report)
Used by tests/test_brain_answer_eval.py (hard gates + floors).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Dict, List

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from puzzle_assistant.brain import Brain  # noqa: E402

DATASET_ID = "SK-BRAIN-EVAL-003"
GUIDE = "Puerto Nube guide"

# key: (text, subject, status, owner, evidence [(source, kind, quote)])
# sources: "guide" (document), "said" (someone's own statement), "notes"
# (Ana's private notes), "luis_notes" (Luis's private notes), "mod" (shared)
CLAIMS: Dict[str, tuple] = {
    # verified (Established)
    "plaza_id": ("The Plaza Nube has room ID 500100.", "Plaza Nube", "verified", "ana",
                 [("guide", "direct", "The Plaza Nube has room ID 500100.")]),
    "plaza_north": ("The north gate of the Plaza Nube leads to the Jardín Nube.", "Plaza Nube",
                    "verified", "ana", [("guide", "direct", "north gate entry")]),
    "molino_alto": ("The Molino Alto has room ID 500501.", "Molino Alto", "verified", "ana",
                    [("guide", "direct", "x")]),
    "molino_bajo": ("The Molino Bajo has room ID 500502.", "Molino Bajo", "verified", "ana",
                    [("guide", "direct", "x")]),
    "faro_none": ("The Faro Viejo has no caretaker.", "Faro Viejo", "verified", "ana",
                  [("guide", "direct", "x")]),
    # unverified, the quote states the same fact by itself (Documented)
    "mira_jardin": ("Mira maintains the Jardín Nube.", "Jardín Nube", "submitted", "ana",
                    [("guide", "direct", "Mira maintains the Jardín Nube.")]),
    "torre_id": ("The Torre Nube has room ID 500200.", "Torre Nube", "awaiting_verification",
                 "ana", [("guide", "confirmation", "The Torre Nube has room ID 500200.")]),
    "faro_nuevo_none": ("The Faro Nuevo has no caretaker.", "Faro Nuevo", "submitted", "ana",
                        [("guide", "direct", "The Faro Nuevo has no caretaker.")]),
    "mercado_mision": ("La misión del Mercado Nube da 40 monedas.", "Mercado Nube", "submitted",
                       "ana", [("guide", "direct", "La misión del Mercado Nube da 40 monedas.")]),
    "nina_notes": ("Nina maintains the Huerta Privada.", "Huerta Privada", "submitted", "ana",
                   [("notes", "direct", "Nina maintains the Huerta Privada.")]),
    # unverified, the quote does NOT state the same fact by itself (Unknown: unsupported)
    "bruno_muelle": ("Bruno maintains the Muelle Nube.", "Muelle Nube", "submitted", "ana",
                     [("guide", "direct", "Carla maintains the Muelle Nube.")]),         # value
    "sala_id": ("The Sala Nube has room ID 500300.", "Sala Nube", "submitted", "ana",
                [("guide", "direct", "The Sala Nube does not have room ID 500300.")]),  # negation
    "tomas_huerto": ("Tomás maintains the Huerto Nube.", "Huerto Nube", "submitted", "ana",
                     [("guide", "direct", "Maybe Tomás maintains the Huerto Nube.")]),   # hedge
    "lena_patio": ("Lena maintains the Patio Nube.", "Patio Nube", "submitted", "ana",
                   [("guide", "direct", "On weekends, Lena maintains the Patio Nube.")]),  # condition
    "ivo_taller": ("Ivo runs the Taller Nube.", "Taller Nube", "submitted", "ana",
                   [("guide", "direct", "Ivo used to run the Taller Nube.")]),          # past
    "sol_cueva": ("Sol maintains the Cueva Nube.", "Cueva Nube", "submitted", "ana",
                  [("guide", "direct", "Sol maintains the Cueva Nube Norte.")]),        # other place
    "eva_club": ("Eva runs the Club Nube.", "Club Nube", "submitted", "ana",
                 [("guide", "direct", "Eva runs the Club Nube and the Bar Nube.")]),    # run-on
    "max_mirador": ("Max maintains the Mirador Nube.", "Mirador Nube", "submitted", "ana",
                    [("guide", "direct", "In the old version, Max maintains the Mirador Nube.")]),
    "gil_arco": ("Gil maintains the Arco Nube.", "Arco Nube", "submitted", "ana",
                 [("guide", "direct", "")]),                                             # empty
    "torre_exit": ("The south exit leads to the Faro Nuevo.", "Torre Nube", "submitted", "ana",
                   [("guide", "direct", "The south exit leads to the Faro Nuevo.")]),   # needs subject
    "noa_bosque": ("Noa maintains the Bosque Nube.", "Bosque Nube", "submitted", "ana",
                   [("said", "direct", "Noa maintains the Bosque Nube.")]),             # own statement
    "pau_lago": ("Pau maintains the Lago Nube.", "Lago Nube", "submitted", "ana",
                 [("guide", "assertion", "Pau maintains the Lago Nube.")]),            # assertion
    "rita_isla": ("Rita maintains the Isla Nube.", "Isla Nube", "submitted", "ana", []),  # none
    # a chain whose first step is supported by its source but the second is not
    "torre_east": ("The east gate of the Torre Nube leads to the Corral Nube.", "Torre Nube",
                   "submitted", "ana",
                   [("guide", "direct", "The east gate of the Torre Nube leads to the Corral Nube.")]),
    "dani_corral": ("Dani maintains the Corral Nube.", "Corral Nube", "submitted", "ana", []),
    # contradiction (both sides quoted - still never an answer)
    "ona_granja": ("Ona maintains the Granja Nube.", "Granja Nube", "submitted", "ana",
                   [("guide", "direct", "Ona maintains the Granja Nube.")]),
    "leo_granja": ("Leo maintains the Granja Nube.", "Granja Nube", "submitted", "ana",
                   [("guide", "direct", "Leo maintains the Granja Nube.")]),
    # outdated only / rejected
    "puente_old": ("The Puente Nube has room ID 500400.", "Puente Nube", "outdated", "ana",
                   [("guide", "direct", "The Puente Nube has room ID 500400.")]),
    "abel_torre": ("Abel maintains the Torre Nube.", "Torre Nube", "rejected", "ana",
                   [("guide", "direct", "Abel maintains the Torre Nube.")]),
    # shared knowledge (approved by the moderator)
    "zoe_plaza": ("Zoe runs the Plaza Nube.", "Plaza Nube", "shared", "mod",
                  [("mod", "direct", "Zoe runs the Plaza Nube.")]),
    # another user's private knowledge
    "kai_jardin": ("Kai maintains the Jardín Nube.", "Jardín Nube", "submitted", "luis",
                   [("luis_notes", "direct", "Kai maintains the Jardín Nube.")]),
}

# (question, asker, expected category, expected reason, expected value)
QUESTIONS: List[tuple] = [
    ("What is the room ID of the Plaza Nube?", "ana", "established", "", "500100"),
    ("Who is the caretaker of the Faro Viejo?", "ana", "established", "", "none"),
    ("Who runs the Plaza Nube?", "ana", "established", "", "Zoe"),
    ("Who maintains the Jardín Nube?", "ana", "documented", "", "Mira"),
    ("Who maintains the room the north gate of the Plaza Nube leads to?", "ana", "documented",
     "", "Mira"),
    ("What is the room ID of the Torre Nube?", "ana", "documented", "", "500200"),
    ("Who is the caretaker of the Faro Nuevo?", "ana", "documented", "", "none"),
    ("¿Cuántas monedas da la misión del Mercado Nube?", "ana", "documented", "", "40 monedas"),
    ("Who maintains the Huerta Privada?", "ana", "documented", "", "Nina"),
    ("Who maintains the Muelle Nube?", "ana", "unknown", "unsupported", None),
    ("What is the room ID of the Sala Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Huerto Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Patio Nube?", "ana", "unknown", "unsupported", None),
    ("Who runs the Taller Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Cueva Nube?", "ana", "unknown", "unsupported", None),
    ("Who runs the Club Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Mirador Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Arco Nube?", "ana", "unknown", "unsupported", None),
    ("Where does the south exit of the Torre Nube lead?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Bosque Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Lago Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the Isla Nube?", "ana", "unknown", "unsupported", None),
    ("Who maintains the room the east gate of the Torre Nube leads to?", "ana", "unknown",
     "unsupported", None),
    ("Where does the east gate of the Torre Nube lead?", "ana", "documented", "", "Corral Nube"),
    ("Who maintains the Granja Nube?", "ana", "unknown", "sources_disagree", None),
    ("What is the room ID of the Puente Nube?", "ana", "unknown", "only_outdated", None),
    ("What is the room ID of the Molino?", "ana", "unknown", "ambiguous", None),
    ("Who maintains the Torre Nube?", "ana", "unknown", "nothing_documented", None),
    ("Who maintains the Faro Viejo?", "ana", "established", "", "none"),
    ("Who is the caretaker of the Plaza Nube?", "ana", "unknown", "nothing_documented", None),
    ("Are pets allowed in the Plaza Nube?", "ana", "unknown", "not_understood", None),
    # Luis: never Ana's knowledge; his own private notes support his own claim
    ("Who maintains the Jardín Nube?", "luis", "documented", "", "Kai"),
    ("What is the room ID of the Plaza Nube?", "luis", "unknown", "nothing_documented", None),
    ("Who maintains the Huerta Privada?", "luis", "unknown", "nothing_documented", None),
    ("Who runs the Plaza Nube?", "luis", "established", "", "Zoe"),
]

# everyday personal-assistant questions: SK cannot answer these yet (B4)
GENERAL: List[tuple] = [
    ("When is my sister's birthday?", "ana", "unknown", "not_understood", None),
    ("What time is my dentist appointment?", "ana", "unknown", "not_understood", None),
    ("¿Dónde dejé las llaves?", "ana", "unknown", "not_understood", None),
    ("What is my bank PIN?", "ana", "unknown", "not_understood", None),
    ("Remind me to call Ana tomorrow", "ana", "unknown", "not_understood", None),
    ("Remember that my locker code is 4412", "ana", "unknown", "not_understood", None),
    ("Who maintains my garden?", "ana", "unknown", "nothing_documented", None),
    ("¿Quién cuida el jardín de mi casa?", "ana", "unknown", "nothing_documented", None),
    ("What is the capital of France?", "ana", "unknown", "not_understood", None),
    ("¿Cuál es la capital de Francia?", "ana", "unknown", "not_understood", None),
]


def build(path: str):
    """The dataset in a new database at `path`. Returns (brain, ids, users)."""
    b = Brain(path, "", "")
    ana = b.local_user
    luis = b.store.add_contributor("Luis (eval)", "external_user")
    mod = b.store.add_contributor("Moderator (eval)", "moderator")
    users = {"ana": ana, "luis": luis, "mod": mod}
    src = {"guide": b.add_source("document", title=GUIDE, reference="eval://" + DATASET_ID,
                                 captured_at="2026-10-01"),
           "said": b.add_source("user_statement", title="Ana said so"),
           "notes": b.add_source("document", title="Ana's notes", private=True),
           "luis_notes": b.add_source("document", title="Luis's notes", private=True, by=luis),
           "mod": b.add_source("document", title="Moderator's guide", by=mod)}
    ids: Dict[str, int] = {}
    for key, (text, subject, status, owner, evidence) in CLAIMS.items():
        who = users[owner]
        cid = b.learn(text, "imported", subject, by=who)
        ids[key] = cid
        for s, kind, quote in evidence:
            b.add_evidence(cid, kind, src[s], quote, by=who)
        if status == "shared":
            b.review(cid, "verified", reviewer=mod)
            b.propose_share(cid, by=mod)
            b.review(cid, "verified", reviewer=mod)
            b.approve_share(cid, mod)
        elif status != "submitted":
            b.review(cid, status, reviewer=who)
    return b, ids, users


def evaluate() -> dict:
    d = tempfile.mkdtemp(prefix="sk_answer_eval_")
    b, ids, users = build(os.path.join(d, "eval.db"))
    rows = []
    try:
        for q, asker, cat, reason, value in QUESTIONS + GENERAL:
            r = b.respond(q, viewer=users[asker])
            ok = (r.category, r.reason) == (cat, reason) and \
                (value is None or r.value == value)
            rows.append({"q": q, "asker": asker, "expect": (cat, reason, value),
                         "got": (r.category, r.reason, r.value), "ok": ok,
                         "sentence": r.sentence})
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)
    return {"rows": rows}


def report(res: dict) -> str:
    rows = res["rows"]
    out = [f"Dataset {DATASET_ID}: {len(CLAIMS)} claims, {len(rows)} questions "
           f"({len(GENERAL)} everyday personal questions)",
           f"  correct category/reason/value: {sum(r['ok'] for r in rows)}/{len(rows)}"]
    for cat in ("established", "documented", "unknown"):
        n = sum(1 for r in rows if r["expect"][0] == cat)
        ok = sum(1 for r in rows if r["expect"][0] == cat and r["ok"])
        out.append(f"  {cat:12} {ok}/{n}")
    false_est = [r for r in rows if r["got"][0] == "established" and r["expect"][0] != "established"]
    false_doc = [r for r in rows if r["got"][0] == "documented" and r["expect"][0] != "documented"]
    out.append(f"  false Established: {len(false_est)}   false Documented: {len(false_doc)}")
    for r in rows:
        if not r["ok"]:
            out.append(f"  WRONG {r['q']!r} ({r['asker']}): expected {r['expect']} got {r['got']}")
    return "\n".join(out)


if __name__ == "__main__":
    print(report(evaluate()))
