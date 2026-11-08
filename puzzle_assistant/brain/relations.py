"""Relations as data (B5).

B3's five relations are built in (their sentence and question forms stay in
facts.py / reason.py, unchanged). More relations can be ADDED as data - a
key, a value type, sentence forms and question forms - without code:

    brain.add_relation("opens_at", "text",
                       sentence_forms=["The {place} opens at {value}"],
                       question_forms=["when does the {place} open"],
                       labels={"en": "when it opens", "es": "cuándo abre"})

B3's forms always win: an added form is only tried when no B3 form matched
the claim (facts.extract_all). Cardinality (one / many) and "changes over
time" are recorded now and used for conflicts and dates in later milestones.
"""
from __future__ import annotations

import re
from typing import Dict

VALUE_TYPES = ("place", "person", "number", "text")
CARDINALITIES = ("one", "many")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

# key -> (value type, cardinality, changes over time, labels)
BUILTIN: Dict[str, tuple] = {
    "leads_to": ("place", "one", False, {"en": "where it leads", "es": "a dónde lleva"}),
    "caretaker": ("person", "one", True, {"en": "who looks after it", "es": "quién lo cuida"}),
    "runs": ("person", "one", True, {"en": "who runs it", "es": "quién lo dirige"}),
    "room_id": ("number", "one", False, {"en": "its room ID", "es": "su ID de sala"}),
    "quest_reward": ("number", "one", True, {"en": "its quest reward",
                                             "es": "la recompensa de su misión"}),
}

FACTS_METHOD = "b3-forms-v1"            # how B3's built-in forms read a claim
