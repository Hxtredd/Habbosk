"""B5.1 question-understanding evaluation (synthetic, separate from user data).

"Isla Brisa" (SK-BRAIN-EVAL-004): places with every built-in relation, a
relation added as data (opens_at), approved / ambiguous / proposed aliases,
two names one letter apart (Torre Alta / Torre Alba), a person who looks
after two rooms, a documented-only claim, a documented absence - and private
knowledge of the asker (Ana) and of another user (Luis).

30 target questions (15 English, 15 Spanish), each asked in many ways: other
word orders (the place first), with and without punctuation and accents,
polite / framing words, small typos in question words, small typos in the
name, and combinations. Every variant has its EXPECTED result, written from
the data (ground truth), not from what the code returns:

  ("answer", category, value)   the target's answer (any wording / order /
                                question-word typo)
  ("did_you_mean", name)        a name with a typo: SK must ask first
  ("ambiguous", [names])        several places fit: SK must ask which
  ("refuse",)                   not answered: not understood or nothing
                                documented - never a value, never a guess

Run:  python tests/brain_understanding_eval.py      (prints the report)
Used by tests/test_brain_understanding.py (hard gates).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unicodedata
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from puzzle_assistant.brain import Brain  # noqa: E402
from puzzle_assistant.brain.store import IMPORTER  # noqa: E402

DATASET_ID = "SK-BRAIN-EVAL-004"
GUIDE = "Isla Brisa guide"

# (text, subject, status, owner)  verified claims carry a direct guide quote
CLAIMS: List[Tuple[str, str, str, str]] = [
    ("Mira maintains the Lantern Garden.", "Lantern Garden", "verified", "ana"),
    ("The Lantern Garden has room ID 300100.", "Lantern Garden", "verified", "ana"),
    ("The quest in the Lantern Garden gives 20 coins.", "Lantern Garden", "verified", "ana"),
    ("Captain Ines runs the Harbour Square.", "Harbour Square", "verified", "ana"),
    ("The Harbour Square has room ID 300200.", "Harbour Square", "verified", "ana"),
    ("The north gate of the Harbour Square leads to the Lantern Garden.", "Harbour Square",
     "verified", "ana"),
    ("Tomás maintains the Mercado Azul.", "Mercado Azul", "verified", "ana"),
    ("La misión del Mercado Azul da 25 monedas.", "Mercado Azul", "verified", "ana"),
    ("The Mercado Azul has room ID 300300.", "Mercado Azul", "verified", "ana"),
    ("Don Ramón runs the Plaza del Puerto.", "Plaza del Puerto", "verified", "ana"),
    ("La salida oeste de la Plaza del Puerto lleva al Mercado Azul.", "Plaza del Puerto",
     "verified", "ana"),
    ("The Plaza del Puerto has room ID 300500.", "Plaza del Puerto", "verified", "ana"),
    ("The Faro Norte has room ID 300401.", "Faro Norte", "verified", "ana"),
    ("The Faro Sur has room ID 300402.", "Faro Sur", "verified", "ana"),
    ("Bruno maintains the Torre Alta.", "Torre Alta", "verified", "ana"),
    ("Carla maintains the Torre Alba.", "Torre Alba", "verified", "ana"),
    ("The Glass Library has no caretaker.", "Glass Library", "verified", "ana"),
    ("The Glass Library opens at nine.", "Glass Library", "verified", "ana"),
    ("The Clock Tower opens at noon.", "Clock Tower", "verified", "ana"),
    ("Nora maintains the Old Pier.", "Old Pier", "verified", "ana"),
    ("Nora maintains the Clock Tower.", "Clock Tower", "verified", "ana"),
    ("Celia runs the Jardín Botánico.", "Jardín Botánico", "verified", "ana"),
    ("El Jardín Botánico tiene el ID 300600.", "Jardín Botánico", "verified", "ana"),
    # rejected: never used, its name never suggested
    ("The Ghost Hall has room ID 300700.", "Ghost Hall", "rejected", "ana"),
    # documented only (an unverified claim whose guide quote states it)
    ("Lio maintains the Rope Shed.", "Rope Shed", "documented", "ana"),
    # private knowledge: Ana's own, and Luis's (never seen by Ana)
    ("Zeta maintains the Secret Cellar.", "Secret Cellar", "verified", "ana_private"),
    ("Kai maintains the Hidden Vault.", "Hidden Vault", "verified", "luis"),
    ("The Hidden Vault has room ID 999111.", "Hidden Vault", "verified", "luis"),
]
OPENS_AT = dict(key="opens_at", value_type="text",
                sentence_forms=["The {place} opens at {value}"],
                question_forms=["when does the {place} open", "what time does the {place} open",
                                "a que hora abre el {place}", "cuando abre el {place}"],
                labels={"en": "when it opens", "es": "cuándo abre"})
# entity -> (approved aliases, aliases only proposed by the importer)
ENTITIES = {"Faro Norte": (["Faro del Norte", "Twin Lights"], []),
            "Faro Sur": (["Twin Lights"], []),
            "Rope Shed": ([], ["Cordage Hall"])}
# words that must never reach anyone but their owner
SECRETS = {"luis": ["Hidden Vault", "Kai", "999111"], "ana": ["Secret Cellar", "Zeta"]}


def build(path: str):
    """The dataset in a new database at `path`. Returns (brain, users)."""
    b = Brain(path, "", "")
    ana = b.local_user
    luis = b.store.add_contributor("Luis (eval)", "external_user")
    mod = b.store.add_contributor("Moderator (eval)", "moderator")
    users = {"ana": ana, "luis": luis, "mod": mod}
    guide = b.add_source("document", title=GUIDE, reference="eval://" + DATASET_ID,
                         captured_at="2026-11-01")
    notes = b.add_source("document", title="Ana's notes", private=True)
    lnotes = b.add_source("document", title="Luis's notes", private=True, by=luis)
    b.add_relation(by=ana, **OPENS_AT)
    for text, subject, status, owner in CLAIMS:
        if owner == "luis":
            c = b.learn(text, "user", subject, by=luis)
            b.add_evidence(c, "direct", lnotes, text, by=luis)
            b.review(c, "verified", reviewer=luis)
        elif owner == "ana_private":
            c = b.learn(text, "user", subject, by=ana)
            b.add_evidence(c, "direct", notes, text, by=ana)
            b.review(c, "verified", reviewer=ana)
        else:
            c = b.learn(text, "imported", subject, by=ana)
            b.add_evidence(c, "direct", guide, text, by=ana)
            if status in ("verified", "rejected"):
                b.review(c, status, reviewer=ana)
    for name, (approved, proposed) in ENTITIES.items():
        e = b.add_entity(name, "room", by=ana)
        for al in approved:
            b.approve_alias(b.add_alias(e, al, by=ana), by=ana)
        for al in proposed:
            b.add_alias(e, al, by=IMPORTER)             # only proposed: never used
    return b, users


# -- the 30 targets --------------------------------------------------------------------
# (id, language, phrasings with {P}, expected answer (category, value), name, typo name)
P = "{P}"
TARGETS: List[tuple] = [
    # ---- English
    ("en_care_lantern", "en", "caretaker", "Lantern Garden", ("established", "Mira")),
    ("en_care_mercado", "en", "caretaker", "Mercado Azul", ("established", "Tomás")),
    ("en_care_glass", "en", "caretaker", "Glass Library", ("established", "none")),
    ("en_care_rope", "en", "caretaker", "Rope Shed", ("documented", "Lio")),
    ("en_runs_harbour", "en", "runs", "Harbour Square", ("established", "Captain Ines")),
    ("en_runs_plaza", "en", "runs", "Plaza del Puerto", ("established", "Don Ramón")),
    ("en_id_lantern", "en", "room_id", "Lantern Garden", ("established", "300100")),
    ("en_id_faro", "en", "room_id", "Faro Norte", ("established", "300401")),
    ("en_id_harbour", "en", "room_id", "Harbour Square", ("established", "300200")),
    ("en_quest_lantern", "en", "quest_reward", "Lantern Garden", ("established", "20 coins")),
    ("en_quest_mercado", "en", "quest_reward", "Mercado Azul", ("established", "25 coins")),
    ("en_north_harbour", "en", "leads_north", "Harbour Square", ("established", "Lantern Garden")),
    ("en_west_plaza", "en", "leads_west", "Plaza del Puerto", ("established", "Mercado Azul")),
    ("en_inv_mira", "en", "inverse", "Mira", ("established", "Lantern Garden")),
    ("en_opens_glass", "en", "opens_at", "Glass Library", ("established", "nine")),
    # ---- Spanish
    ("es_care_torre", "es", "caretaker", "Torre Alta", ("established", "Bruno")),
    ("es_care_mercado", "es", "caretaker", "Mercado Azul", ("established", "Tomás")),
    ("es_care_glass", "es", "caretaker", "Glass Library", ("established", "ninguno")),
    ("es_care_rope", "es", "caretaker", "Rope Shed", ("documented", "Lio")),
    ("es_runs_plaza", "es", "runs", "Plaza del Puerto", ("established", "Don Ramón")),
    ("es_runs_jardin", "es", "runs", "Jardín Botánico", ("established", "Celia")),
    ("es_id_jardin", "es", "room_id", "Jardín Botánico", ("established", "300600")),
    ("es_id_mercado", "es", "room_id", "Mercado Azul", ("established", "300300")),
    ("es_id_faro", "es", "room_id", "Faro del Norte", ("established", "300401")),
    ("es_quest_mercado", "es", "quest_reward", "Mercado Azul", ("established", "25 monedas")),
    ("es_quest_lantern", "es", "quest_reward", "Lantern Garden", ("established", "20 monedas")),
    ("es_west_plaza", "es", "leads_west", "Plaza del Puerto", ("established", "Mercado Azul")),
    ("es_north_harbour", "es", "leads_north", "Harbour Square", ("established", "Lantern Garden")),
    ("es_inv_bruno", "es", "inverse", "Bruno", ("established", "Torre Alta")),
    ("es_opens_glass", "es", "opens_at", "Glass Library", ("established", "nine")),
]

# phrasings: natural wordings and word orders; {P} is the name
PHRASES: Dict[Tuple[str, str], List[str]] = {
    ("en", "caretaker"): [
        "Who maintains the {P}?", "Who looks after the {P}?", "Who takes care of the {P}?",
        "Who is the caretaker of the {P}?", "{P}: who maintains it?",
        "The {P} - who looks after it?", "{P} caretaker", "Who's the keeper of the {P}?",
        "Tell me who maintains the {P}", "Can you tell me who looks after the {P}, please?",
        "Do you know who takes care of the {P}?", "The {P}'s caretaker?", "caretaker of {P}",
        "who cares for the {P}"],
    ("en", "runs"): [
        "Who runs the {P}?", "Who manages the {P}?", "Who is the manager of the {P}?",
        "{P}: who runs it?", "The {P} - who manages it?", "{P} manager",
        "Tell me who runs the {P}", "Do you know who manages the {P}?", "Who's running the {P}?",
        "The {P}'s manager?", "who runs {P} please", "Could you tell me who manages the {P}?"],
    ("en", "room_id"): [
        "What is the room ID of the {P}?", "What's the {P}'s room ID?", "{P} room ID",
        "Room ID of the {P}?", "The {P} - what is its ID?",
        "Which room number does the {P} have?", "Tell me the room code of the {P}",
        "ID of the {P} please", "{P}: room number?", "Do you know the {P} room ID?",
        "What ID does the {P} have?", "the {P} id"],
    ("en", "quest_reward"): [
        "How many coins does the quest in the {P} give?", "How many coins does the {P} quest give?",
        "{P} quest reward", "What is the quest reward in the {P}?",
        "The {P} quest - how many coins?", "Coins for the {P} quest?",
        "What does the {P} quest pay?", "Tell me the reward of the {P} quest",
        "quest reward {P} please", "How much does the {P} quest give?",
        "{P}: how many coins does the quest give?", "What's the reward for the quest in the {P}?"],
    ("en", "leads_north"): [
        "Where does the north gate of the {P} lead?", "Where does the {P}'s north gate lead?",
        "{P} north gate - where does it lead?", "Where does the north exit of the {P} go?",
        "The {P}: where does the north door lead?", "north exit of {P} leads where?",
        "Tell me where the north gate of the {P} leads",
        "Which room does the north gate of the {P} lead to?",
        "Do you know where the north gate of the {P} leads?",
        "where does the north gate from the {P} lead", "{P}, north exit, where to?",
        "Where does the {P} north path lead?"],
    ("en", "leads_west"): [
        "Where does the west exit of the {P} lead?", "Where does the {P}'s west exit lead?",
        "{P} west exit - where does it go?", "Where does the west door of the {P} go?",
        "The {P}: where does the west exit lead?", "west exit of {P} leads where?",
        "Tell me where the west exit of the {P} leads",
        "Which room does the west exit of the {P} lead to?",
        "Do you know where the west exit of the {P} leads?",
        "where does the west gate from the {P} lead", "{P}, west exit, where to?",
        "Where does the {P} west path lead?"],
    ("en", "inverse"): [
        "Which room does {P} look after?", "What does {P} maintain?",
        "Which place does {P} take care of?", "{P} looks after which room?",
        "{P}: which room does she maintain?", "What room is {P} the caretaker of?",
        "Tell me which room {P} looks after", "Where is {P} the caretaker?",
        "Which room is maintained by {P}?", "Which room is looked after by {P}?",
        "Do you know which room {P} maintains?", "what room does {P} take care of"],
    ("en", "opens_at"): [
        "When does the {P} open?", "What time does the {P} open?", "{P} - when does it open?",
        "When is the {P} open?", "{P} opening time", "The {P}: what time does it open?",
        "Tell me when the {P} opens", "Do you know when the {P} opens?",
        "when does {P} open please", "What time is the {P} open?",
        "Could you tell me when the {P} opens?", "{P}, when does it open?"],
    ("es", "caretaker"): [
        "¿Quién cuida el {P}?", "¿Quién mantiene el {P}?", "¿Quién es el encargado del {P}?",
        "¿Quién se encarga del {P}?", "El {P}, ¿quién lo cuida?", "{P}: ¿quién lo mantiene?",
        "Dime quién cuida el {P}", "¿Sabes quién es el encargado del {P}?",
        "Oye, ¿quién cuida el {P}?", "¿Y el {P}? ¿Quién lo cuida?", "encargado del {P}",
        "¿Quién es el cuidador del {P}?", "¿El {P} quién lo mantiene?",
        "Por favor, ¿quién cuida el {P}?"],
    ("es", "runs"): [
        "¿Quién dirige el {P}?", "¿Quién gestiona el {P}?", "¿Quién es el director del {P}?",
        "El {P}, ¿quién lo dirige?", "{P}: ¿quién lo gestiona?", "Dime quién dirige el {P}",
        "¿Sabes quién dirige el {P}?", "Oye, ¿quién gestiona el {P}?", "director del {P}",
        "¿Quién es el gerente del {P}?", "¿El {P} quién lo dirige?",
        "Por favor, ¿quién administra el {P}?"],
    ("es", "room_id"): [
        "¿Cuál es el ID de sala del {P}?", "¿Cuál es el ID del {P}?", "ID del {P}",
        "¿Qué ID tiene el {P}?", "El {P}, ¿qué número de sala tiene?",
        "{P}: ¿cuál es su código?", "Dime el ID de sala del {P}", "¿Sabes el número del {P}?",
        "número de sala del {P}", "¿Me dices el código del {P}?",
        "Oye, ¿cuál es el ID del {P}?", "¿El {P} qué ID tiene?"],
    ("es", "quest_reward"): [
        "¿Cuántas monedas da la misión del {P}?", "¿Cuántas monedas da la misión en el {P}?",
        "La misión del {P}, ¿cuántas monedas da?", "¿Cuál es la recompensa de la misión del {P}?",
        "recompensa de la misión del {P}", "Dime cuántas monedas da la misión del {P}",
        "¿Cuánto paga la misión del {P}?", "{P}: ¿cuántas monedas da la misión?",
        "¿Sabes cuántas monedas da la misión del {P}?", "monedas misión {P}",
        "Oye, ¿qué recompensa da la misión del {P}?", "¿La misión del {P} cuántas monedas da?"],
    ("es", "leads_west"): [
        "¿A dónde lleva la salida oeste del {P}?", "¿Adónde lleva la puerta oeste del {P}?",
        "La salida oeste del {P}, ¿a dónde lleva?", "¿A dónde va la salida oeste del {P}?",
        "{P}: ¿a dónde lleva la puerta oeste?", "Dime a dónde lleva la salida oeste del {P}",
        "¿Sabes a dónde conduce la salida oeste del {P}?", "salida oeste del {P}",
        "Oye, ¿adónde lleva la salida oeste del {P}?", "¿La puerta oeste del {P} a dónde lleva?",
        "¿A qué sala lleva la salida oeste del {P}?", "¿Dónde lleva el camino oeste del {P}?"],
    ("es", "leads_north"): [
        "¿A dónde lleva la salida norte del {P}?", "¿Adónde lleva la puerta norte del {P}?",
        "La salida norte del {P}, ¿a dónde lleva?", "¿A dónde va la salida norte del {P}?",
        "{P}: ¿a dónde lleva la puerta norte?", "Dime a dónde lleva la salida norte del {P}",
        "¿Sabes a dónde conduce la salida norte del {P}?", "salida norte del {P}",
        "Oye, ¿adónde lleva la salida norte del {P}?", "¿La puerta norte del {P} a dónde lleva?",
        "¿A qué sala lleva la salida norte del {P}?", "¿Dónde lleva el camino norte del {P}?"],
    ("es", "inverse"): [
        "¿Qué sala cuida {P}?", "¿Qué lugar mantiene {P}?", "¿De qué sala es encargado {P}?",
        "{P}, ¿qué sala cuida?", "¿Cuál es la sala que cuida {P}?", "Dime qué sala cuida {P}",
        "¿Dónde es {P} el encargado?", "¿Sabes qué sala mantiene {P}?", "{P} cuida qué sala",
        "Oye, ¿qué sala cuida {P}?", "¿Qué sala mantiene {P}?", "¿{P} qué lugar cuida?"],
    ("es", "opens_at"): [
        "¿A qué hora abre el {P}?", "¿Cuándo abre el {P}?", "El {P}, ¿a qué hora abre?",
        "{P}: ¿cuándo abre?", "Dime a qué hora abre el {P}", "¿Sabes cuándo abre el {P}?",
        "Oye, ¿a qué hora abre el {P}?", "¿A qué hora abre {P}?", "¿El {P} a qué hora abre?", "¿Y el {P}? ¿Cuándo abre?",
        "Por favor, ¿cuándo abre el {P}?", "cuando abre el {P}"],
}

# question-word typos: (language, relation) -> phrasings with a typo, read the same way
TYPOS: Dict[Tuple[str, str], List[str]] = {
    ("en", "caretaker"): ["Who mantains the {P}?", "Who maintians the {P}?",
                          "Who is the caretakr of the {P}?", "Who loks after the {P}?"],
    ("en", "runs"): ["Who manges the {P}?", "Who is the manger of the {P}?",
                     "Who runns the {P}?"],
    ("en", "room_id"): ["What is the room ID of the {P} plese?", "What is the rooom ID of the {P}?",
                        "{P} room idnetifier"],
    ("en", "quest_reward"): ["How many cions does the quest in the {P} give?",
                             "{P} qeust reward", "What is the quest rewrad in the {P}?"],
    ("en", "leads_north"): ["Where does the north gtae of the {P} lead?",
                            "Wehre does the north exit of the {P} lead?",
                            "Where does the noth gate of the {P} lead?"],
    ("en", "leads_west"): ["Where does the west exti of the {P} lead?",
                           "Wehre does the west exit of the {P} lead?"],
    ("en", "inverse"): ["Which room does {P} mantain?", "Whcih room does {P} look after?"],
    ("en", "opens_at"): ["When does the {P} opne?", "Wehn does the {P} open?"],
    ("es", "caretaker"): ["¿Quién cuidda el {P}?", "¿Quién es el encrgado del {P}?",
                          "¿Quién mantine el {P}?", "¿Qiuén cuida el {P}?"],
    ("es", "runs"): ["¿Quién dirije el {P}?", "¿Quién gestina el {P}?"],
    ("es", "room_id"): ["¿Cuál es el códgio del {P}?", "¿Cuál es el nmero de sala del {P}?"],
    ("es", "quest_reward"): ["¿Cuántas monedsa da la misión del {P}?",
                             "¿Cuántas monedas da la misoin del {P}?"],
    ("es", "leads_west"): ["¿A dónde lleva la sailda oeste del {P}?",
                           "¿A dónde lleva la salida oetse del {P}?"],
    ("es", "leads_north"): ["¿A dónde lleva la sailda norte del {P}?",
                            "¿A dónde lleva la puetra norte del {P}?"],
    ("es", "inverse"): ["¿Qué sala cuidda {P}?", "¿Qué sala manteine {P}?"],
    ("es", "opens_at"): ["¿A qué hora arbe el {P}?", "¿Cuándo arbe el {P}?"],
}


def _plain(s: str) -> str:
    """lower case, no punctuation, no accents (how people type in a hurry)."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in s).lower().split())


def name_typos(name: str) -> List[str]:
    """Small typos of a name: a letter dropped, two letters swapped, a letter
    doubled - in its longest word (5+ letters), never in short words."""
    words = name.split()
    i = max(range(len(words)), key=lambda n: len(words[n]))
    w = words[i]
    if len(w) < 5:
        return []
    out = []
    for t in (w[:-2] + w[-1], w[:2] + w[3] + w[2] + w[4:], w[:3] + w[3] + w[3:]):
        if t.lower() != w.lower():
            out.append(" ".join(words[:i] + [t] + words[i + 1:]))
    return out


def variants(target) -> List[Tuple[str, tuple]]:
    """Every way the target is asked, each with its expected result."""
    tid, lang, rel, name, (cat, value) = target
    answer = ("answer", cat, value)
    out = []
    for n, ph in enumerate(PHRASES[(lang, rel)]):
        q = ph.replace(P, name)
        out.append((q, answer))
        if n % 2 == 0:
            out.append((_plain(q), answer))          # no punctuation, no accents, lower case
        if n % 3 == 0:
            out.append((q.upper(), answer))
    for ph in TYPOS.get((lang, rel), []):
        out.append((ph.replace(P, name), answer))
        out.append((_plain(ph.replace(P, name)), answer))
    shown = name
    for n, typo in enumerate(name_typos(name)):
        for ph in PHRASES[(lang, rel)][:2] + PHRASES[(lang, rel)][4:5]:
            out.append((ph.replace(P, typo), ("did_you_mean", shown)))
        # with a question-word typo as well
        for ph in TYPOS.get((lang, rel), [])[:1]:
            out.append((ph.replace(P, typo), ("did_you_mean", shown)))
    return out


# -- false matches, refusals, ambiguity (expected from the data) -----------------------------
PROBES: List[Tuple[str, str, tuple]] = [
    # partial / near names
    ("What is the room ID of the Faro?", "ana", ("ambiguous", ["Faro Norte", "Faro Sur"])),
    ("Faro room id", "ana", ("ambiguous", ["Faro Norte", "Faro Sur"])),
    ("¿Cuál es el ID del Faro?", "ana", ("ambiguous", ["Faro Norte", "Faro Sur"])),
    ("Twin Lights room ID", "ana", ("ambiguous", ["Faro Norte", "Faro Sur"])),
    ("Faro Nort room ID", "ana", ("did_you_mean", "Faro Norte")),
    ("Faro Su room ID", "ana", ("ambiguous", ["Faro Norte", "Faro Sur"])),
    ("Who maintains the Torre Alda?", "ana", ("ambiguous", ["Torre Alba", "Torre Alta"])),
    ("Torre Alda caretaker", "ana", ("ambiguous", ["Torre Alba", "Torre Alta"])),
    ("Who maintains the Tore Alta?", "ana", ("did_you_mean", "Torre Alta")),
    ("Who maintains the Torre?", "ana", ("ambiguous", ["Torre Alba", "Torre Alta"])),
    ("Who maintains the Lantern Gardens?", "ana", ("did_you_mean", "Lantern Garden")),
    ("Lantern caretaker", "ana", ("refuse",)),                     # part of ONE name
    ("Garden caretaker", "ana", ("refuse",)),
    ("Who maintains the Glass?", "ana", ("refuse",)),
    ("Who maintains the Library?", "ana", ("refuse",)),
    ("Cordage Hall room ID", "ana", ("refuse",)),                   # proposed alias: unused
    ("What is the room ID of the Cordage Hall?", "ana", ("refuse",)),
    ("Who maintains the Lantern Gardenia Park?", "ana", ("refuse",)),
    ("What is the room ID of the Lantern Garden 2?", "ana", ("refuse",)),
    ("Old Peir caretaker", "ana", ("did_you_mean", "Old Pier")),    # 3-letter word: no
    ("Old Per caretaker", "ana", ("refuse",)),
    ("Who maintains the Old Pie?", "ana", ("refuse",)),
    ("Room 300100 caretaker", "ana", ("refuse",)),
    ("Who maintains room 300101?", "ana", ("refuse",)),
    ("What is the room ID of the Mercado Azu?", "ana", ("refuse",)),  # 3 letters: exact only
    ("Which room does Nora look after?", "ana", ("ambiguous", ["Clock Tower", "Old Pier"])),
    ("Which room does Mra look after?", "ana", ("refuse",)),           # 3 letters: exact only
    # negations, two places, extra words, the past / future, yes / no, other directions
    ("Who doesn't maintain the Lantern Garden?", "ana", ("refuse",)),
    ("Who does not look after the Lantern Garden?", "ana", ("refuse",)),
    ("Lantern Garden: who never maintains it?", "ana", ("refuse",)),
    ("¿Quién no cuida el Lantern Garden?", "ana", ("refuse",)),
    ("Lantern Garden, ¿quién nunca lo cuida?", "ana", ("refuse",)),
    ("Who maintains the Lantern Garden and the Rope Shed?", "ana", ("refuse",)),
    ("Lantern Garden or Mercado Azul caretaker", "ana", ("refuse",)),
    ("Lantern Garden caretaker Mercado Azul", "ana", ("refuse",)),
    ("Who maintains the room next to the Lantern Garden?", "ana", ("refuse",)),
    ("Who maintains the shop behind the Lantern Garden?", "ana", ("refuse",)),
    ("Lantern Garden caretaker phone number", "ana", ("refuse",)),
    ("Who maintained the Lantern Garden?", "ana", ("refuse",)),
    ("Who will maintain the Lantern Garden?", "ana", ("refuse",)),
    ("Who maintains the Lantern Garden tomorrow?", "ana", ("refuse",)),
    ("Who used to run the Harbour Square?", "ana", ("refuse",)),
    ("¿Quién cuidaba el Lantern Garden?", "ana", ("refuse",)),
    ("¿Quién cuidará el Lantern Garden?", "ana", ("refuse",)),
    ("Does the Lantern Garden have a caretaker?", "ana", ("refuse",)),
    ("Does Mira maintain the Lantern Garden?", "ana", ("refuse",)),
    ("Is Mira the caretaker of the Lantern Garden?", "ana", ("refuse",)),
    ("¿Tiene encargado el Lantern Garden?", "ana", ("refuse",)),
    ("When is the Lantern Garden maintained?", "ana", ("refuse",)),
    ("Where is the caretaker of the Lantern Garden?", "ana", ("refuse",)),
    ("How is the Lantern Garden maintained?", "ana", ("refuse",)),
    ("Who is the room ID of the Lantern Garden?", "ana", ("refuse",)),
    ("Who does Mira look after?", "ana", ("refuse",)),
    ("What leads to the Lantern Garden?", "ana", ("refuse",)),
    ("Which exit leads to the Lantern Garden?", "ana", ("refuse",)),
    ("What connects to the Mercado Azul?", "ana", ("refuse",)),
    ("¿Qué salida lleva al Mercado Azul?", "ana", ("refuse",)),
    # ... including places whose OWN exits are documented (read the wrong way
    # round, these would be answered with where their exit leads)
    ("What leads to the Harbour Square?", "ana", ("refuse",)),
    ("Which exit leads to the Harbour Square?", "ana", ("refuse",)),
    ("Which gate goes to the Harbour Square?", "ana", ("refuse",)),
    ("¿Qué salida lleva a la Plaza del Puerto?", "ana", ("refuse",)),
    ("¿Qué camino va hacia la Plaza del Puerto?", "ana", ("refuse",)),
    # a typo one letter from two KINDS of word ("doom": room / door; "salda":
    # sala / salida): never read either way
    ("Lantern Garden doom keeper", "ana", ("refuse",)),
    ("¿Quién cuida la salda del Lantern Garden?", "ana", ("refuse",)),
    # names from rejected claims and proposed aliases are never suggested
    ("Cordage Hal room ID", "ana", ("refuse",)),
    ("Ghost Hal room ID", "ana", ("refuse",)),
    ("Ghost Hall room ID", "ana", ("refuse",)),
    ("Who maintains the room the north gate of the Harbour Square leads to?", "ana",
     ("answer", "established", "Mira")),                             # B3's own chain
    ("Who looks after the room the Harbour Square north gate leads to?", "ana",
     ("answer", "established", "Mira")),                             # B3's own chain
    ("Who manages the room the Harbour Square north gate leads to?", "ana", ("refuse",)),
    ("Wht is the room ID of the Lantern Garden?", "ana", ("refuse",)),  # 3 letters: exact only
    # "nort" is one letter from "north" AND from the person Nora: refused, never guessed
    ("Where does the nort gate of the Harbour Square lead?", "ana", ("refuse",)),
    ("Harbour Square north and south exits", "ana", ("refuse",)),
    ("Who maintains and runs the Lantern Garden?", "ana", ("refuse",)),
    ("Who is in charge of the Lantern Garden?", "ana",
     ("ambiguous", ["who looks after the Lantern Garden", "who runs the Lantern Garden"])),
    ("Lantern Garden", "ana", ("refuse",)),
    ("Mira", "ana", ("refuse",)),
    ("Tell me about the Lantern Garden", "ana", ("refuse",)),
    ("Are pets allowed in the Lantern Garden?", "ana", ("refuse",)),
    ("Who maintains my garden?", "ana", ("refuse",)),
    ("What time is my dentist appointment?", "ana", ("refuse",)),
    ("¿Dónde dejé las llaves?", "ana", ("refuse",)),
    ("Who paints the Lantern Garden?", "ana", ("refuse",)),
    ("Who owns the Lantern Garden?", "ana", ("refuse",)),
    ("Who visits the Lantern Garden?", "ana", ("refuse",)),
    ("What is the Lantern Garden?", "ana", ("refuse",)),
    # privacy: nobody is ever offered a name they may not see
    ("Who maintains the Hidden Vault?", "ana", ("refuse",)),
    ("Who maintains the Hiden Vault?", "ana", ("refuse",)),
    ("Hidden Vaul room ID", "ana", ("refuse",)),
    ("Which room does Kai look after?", "ana", ("refuse",)),
    ("Who maintains the Secret Cellar?", "luis", ("refuse",)),
    ("Who maintains the Secret Celar?", "luis", ("refuse",)),
    ("Secret Celar caretaker", "luis", ("refuse",)),
    ("Which room does Zeta look after?", "luis", ("refuse",)),
    ("Who maintains the Secret Celar?", "ana", ("did_you_mean", "Secret Cellar")),  # her own
    ("Who maintains the Hiden Vault?", "luis", ("did_you_mean", "Hidden Vault")),    # his own
    ("Who maintains the Secret Cellar?", "ana", ("answer", "established", "Zeta")),
    ("Hidden Vault room ID", "luis", ("answer", "established", "999111")),
]

# HELD-OUT: written before its first run, expectations = the IDEAL answer from
# the data (a refusal where SK should refuse). Not tuned against: a miss here
# shows a wording SK does not understand yet; only a WRONG answer fails a gate.
A_ = ("answer", "established")
HELDOUT: List[Tuple[str, str, tuple]] = [
    ("Lantern Garden - who's the caretaker?", "ana", A_ + ("Mira",)),
    ("hey sk who maintains the mercado azul", "ana", A_ + ("Tomás",)),
    ("Mercado Azul, who takes care of it please?", "ana", A_ + ("Tomás",)),
    ("caretaker for the Torre Alta?", "ana", A_ + ("Bruno",)),
    ("The Torre Alba caretaker is who?", "ana", A_ + ("Carla",)),
    ("Who keeps the Old Pier?", "ana", A_ + ("Nora",)),
    ("Who looks after Old Pier", "ana", A_ + ("Nora",)),
    ("Jardín Botánico manager?", "ana", A_ + ("Celia",)),
    ("who manages jardin botanico", "ana", A_ + ("Celia",)),
    ("Captain Ines runs which room?", "ana", A_ + ("Harbour Square",)),
    ("Which place does Don Ramón run?", "ana", A_ + ("Plaza del Puerto",)),
    ("What does Nora look after?", "ana", ("ambiguous", ["Clock Tower", "Old Pier"])),
    ("room id for the Mercado Azul", "ana", A_ + ("300300",)),
    ("Mercado Azul's ID please", "ana", A_ + ("300300",)),
    ("what's the id of plaza del puerto", "ana", A_ + ("300500",)),
    ("Faro Sur ID?", "ana", A_ + ("300402",)),
    ("Faro del Norte - room ID?", "ana", A_ + ("300401",)),
    ("Twin Lights ID", "ana", ("ambiguous", ["Faro Norte", "Faro Sur"])),
    ("Lantern Garden coins", "ana", A_ + ("20 coins",)),
    ("How many coins is the Lantern Garden quest worth?", "ana", A_ + ("20 coins",)),
    ("Mercado Azul quest coins?", "ana", A_ + ("25 coins",)),
    ("Where does Harbour Square's north gate go?", "ana", A_ + ("Lantern Garden",)),
    ("Harbour Square: where does the north exit take you?", "ana", A_ + ("Lantern Garden",)),
    ("West exit of Plaza del Puerto?", "ana", A_ + ("Mercado Azul",)),
    ("When does the Clock Tower open?", "ana", A_ + ("noon",)),
    ("Clock Tower: what time does it open?", "ana", A_ + ("noon",)),
    ("Glass Library open time", "ana", A_ + ("nine",)),
    ("Glass Library hours", "ana", A_ + ("nine",)),
    ("Who maintans the Torre Alta?", "ana", A_ + ("Bruno",)),
    ("Who maintians the Torre Alba?", "ana", A_ + ("Carla",)),
    ("Who looks after the Lantren Garden?", "ana", ("did_you_mean", "Lantern Garden")),
    ("Mercado Asul room ID", "ana", ("did_you_mean", "Mercado Azul")),
    ("Harbur Square manager", "ana", ("did_you_mean", "Harbour Square")),
    ("Who runs the Harbour Sqaure?", "ana", ("did_you_mean", "Harbour Square")),
    ("Plaza del Puerta west exit", "ana", ("did_you_mean", "Plaza del Puerto")),
    ("Glass Libray opening time", "ana", ("did_you_mean", "Glass Library")),
    ("Faro Nrte room ID", "ana", ("did_you_mean", "Faro Norte")),
    ("Who maintains the Torre Alt?", "ana", ("refuse",)),
    ("Who doesn't run the Harbour Square?", "ana", ("refuse",)),
    ("Who isn't the caretaker of the Old Pier?", "ana", ("refuse",)),
    ("Who ran the Harbour Square?", "ana", ("refuse",)),
    ("Who will run the Plaza del Puerto?", "ana", ("refuse",)),
    ("Does the Harbour Square have a manager?", "ana", ("refuse",)),
    ("Is the Glass Library open?", "ana", ("refuse",)),
    ("Who maintains the Lantern Garden or the Old Pier?", "ana", ("refuse",)),
    ("Mira and Nora rooms", "ana", ("refuse",)),
    ("Which room leads to the Lantern Garden?", "ana", ("refuse",)),
    ("Who runs the café in the Harbour Square?", "ana", ("refuse",)),
    ("What is the Lantern Garden room ID and caretaker?", "ana", ("refuse",)),
    ("Who is the best caretaker of the Lantern Garden?", "ana", ("refuse",)),
    ("Who maintains the Lantern Garden at night?", "ana", ("refuse",)),
    ("Who is the caretaker of Mira?", "ana", ("refuse",)),
    ("What is Mira's room ID?", "ana", ("refuse",)),
    ("Who maintains the Lantern Garden?", "luis", ("refuse",)),
    ("Lantern Gardn caretaker", "luis", ("refuse",)),
    ("Which room does Mira look after?", "luis", ("refuse",)),
    ("Faro room id", "luis", ("refuse",)),
    ("Twin Lights room ID", "luis", ("refuse",)),
    ("¿Quién cuida la Torre Alta?", "ana", A_ + ("Bruno",)),
    ("la torre alba, quien la mantiene", "ana", A_ + ("Carla",)),
    ("¿Quién es la encargada de la Torre Alba?", "ana", A_ + ("Carla",)),
    ("Torre Alta: ¿encargado?", "ana", A_ + ("Bruno",)),
    ("¿Quién lleva la Plaza del Puerto?", "ana", A_ + ("Don Ramón",)),
    ("¿Quién dirige el Jardín Botánico?", "ana", A_ + ("Celia",)),
    ("jardin botanico quien lo gestiona", "ana", A_ + ("Celia",)),
    ("¿Qué sala dirige Celia?", "ana", A_ + ("Jardín Botánico",)),
    ("¿Qué cuida Nora?", "ana", ("ambiguous", ["Clock Tower", "Old Pier"])),
    ("¿Cuál es el número del Mercado Azul?", "ana", A_ + ("300300",)),
    ("el id de la plaza del puerto", "ana", A_ + ("300500",)),
    ("¿Qué código tiene el Faro Sur?", "ana", A_ + ("300402",)),
    ("¿Y el código del Faro del Norte?", "ana", A_ + ("300401",)),
    ("¿Cuántas monedas da el Lantern Garden?", "ana", A_ + ("20 monedas",)),
    ("misión del Mercado Azul, ¿cuánto da?", "ana", A_ + ("25 monedas",)),
    ("¿A dónde lleva la puerta norte del Harbour Square?", "ana", A_ + ("Lantern Garden",)),
    ("¿Adónde va el camino oeste de la Plaza del Puerto?", "ana", A_ + ("Mercado Azul",)),
    ("¿Cuándo abre la Clock Tower?", "ana", A_ + ("noon",)),
    ("¿A qué hora abre la Glass Library?", "ana", A_ + ("nine",)),
    ("¿Quién cuida el Mercado Asul?", "ana", ("did_you_mean", "Mercado Azul")),
    ("¿Quién dirige la Plaza del Pueto?", "ana", ("did_you_mean", "Plaza del Puerto")),
    ("¿Cuál es el ID del Jardín Botánco?", "ana", ("did_you_mean", "Jardín Botánico")),
    ("¿Quién mantien el Old Pier?", "ana", A_ + ("Nora",)),
    ("¿Qiuén es el encargado de la Torre Alta?", "ana", A_ + ("Bruno",)),
    ("¿Quién no dirige la Plaza del Puerto?", "ana", ("refuse",)),
    ("¿Quién dirigía la Plaza del Puerto?", "ana", ("refuse",)),
    ("¿Quién dirigirá la Plaza del Puerto?", "ana", ("refuse",)),
    ("¿Tiene la Torre Alta encargado?", "ana", ("refuse",)),
    ("¿Quién cuida la Torre Alta y la Torre Alba?", "ana", ("refuse",)),
    ("¿Qué sala lleva al Lantern Garden?", "ana", ("refuse",)),
    ("¿Quién cuida el jardín de mi casa?", "ana", ("refuse",)),
    ("¿Cuál es la capital de Francia?", "ana", ("refuse",)),
]

# reply language: (question, expected)
LANGUAGE: List[Tuple[str, str]] = [(q, "es") for q in (
    "¿Quién cuida el Lantern Garden?", "Quien cuida el Lantern Garden", "quien mantiene el faro norte",
    "El Lantern Garden, ¿quién lo cuida?", "Lantern Garden quién lo cuida",
    "encargado del Lantern Garden", "Dime quién dirige el Harbour Square",
    "Oye, ¿cuál es el ID del Mercado Azul?", "cual es el id del mercado azul",
    "¿Qué ID tiene el Faro Norte?", "Faro Norte: ¿cuál es su código?", "numero de sala del Faro Sur",
    "¿Cuántas monedas da la misión del Lantern Garden?", "cuantas monedas da la mision del mercado azul",
    "monedas misión Lantern Garden", "¿A dónde lleva la salida oeste de la Plaza del Puerto?",
    "a donde lleva la salida norte del harbour square", "La salida norte del Harbour Square, ¿a dónde lleva?",
    "¿Qué sala cuida Mira?", "que sala cuida mira", "¿A qué hora abre la Glass Library?",
    "cuando abre la glass library", "Por favor, ¿quién cuida el Rope Shed?", "¿Sabes quién dirige la Plaza del Puerto?",
    "dime el id del jardin botanico", "¡Hola! ¿Quién cuida el Old Pier?", "¿Y el Old Pier?",
    "quién es el encargado de la Torre Alta", "la torre alta quien la cuida", "¿El Mercado Azul qué ID tiene?",
    "recompensa de la misión del Mercado Azul", "puerta norte del Harbour Square a donde lleva",
    "Glass Library a qué hora abre", "¿Cuál es el código de la Torre Alba?", "id de sala de la torre alba",
    "quien gestiona el harbour square", "Mira, ¿qué sala cuida?", "¿Qué lugar mantiene Nora?",
    "¿Dónde es Bruno el encargado?", "¿Me dices el código del Faro Sur?")] + \
    [(q, "en") for q in (
    "Who maintains the Plaza del Puerto?", "What is the room ID of the Jardín Botánico?",
    "Jardín Botánico room ID", "Who runs the Jardín Botánico?", "Plaza del Puerto west exit, where does it lead?",
    "Mercado Azul quest reward", "How many coins does the Mercado Azul quest give?", "Faro del Norte room ID",
    "Faro Norte room ID", "Torre Alta caretaker", "Who looks after the Torre Alba?", "Faro Sur room number",
    "Which room does Tomás look after?", "Who maintains the Lantern Garden?", "lantern garden caretaker",
    "the glass library opening time", "When does the Glass Library open?", "Rope Shed caretaker please",
    "Can you tell me who runs the Harbour Square?", "Do you know the Mercado Azul room ID?",
    "Where does the west exit of the Plaza del Puerto lead?", "Who is the caretaker of the Mercado Azul?",
    "What's the Plaza del Puerto's room ID?", "Tell me who maintains the Torre Alta",
    "Room ID of the Faro del Norte?", "Who manages the Plaza del Puerto?", "Mercado Azul: who looks after it?",
    "The Jardín Botánico - who runs it?", "Which room does Don Ramón run?", "What does Celia run?",
    "Who takes care of the Old Pier?", "What time does the Glass Library open?", "Who runs the Plaza del Puerto please",
    "Is there a quest in the Mercado Azul?", "What is the Mercado Azul?", "Who maintains the Faro Sur?",
    "Old Pier caretaker", "Where does the Harbour Square's north gate lead?", "Clock Tower opening time",
    "Who's the keeper of the Torre Alta?",
    # only a Spanish-looking NAME: its words are not counted
    "Faro del Norte ID?", "Plaza del Puerto ID?", "Jardín Botánico ID", "Torre Alta, Faro Sur")]


def check(r, expect) -> Tuple[bool, str]:
    """Does the response `r` match `expect`? (ok, why not)."""
    kind = expect[0]
    if kind == "answer":
        ok = (r.category, r.value) == (expect[1], expect[2])
        return ok, "" if ok else f"expected {expect[1]} {expect[2]!r}, got {r.category}/{r.reason} {r.value!r}"
    if r.value is not None or r.category != "unknown":
        return False, f"ANSWERED {r.category} {r.value!r} where {kind} was expected"
    if kind == "did_you_mean":
        ok = r.reason == "did_you_mean" and r.clarification["options"] == [expect[1]]
        return ok, "" if ok else f"expected did_you_mean {expect[1]}, got {r.reason} {r.clarification}"
    if kind == "ambiguous":
        ok = r.reason == "ambiguous" and r.clarification and \
            sorted(r.clarification["options"]) == sorted(expect[1])
        return ok, "" if ok else f"expected ambiguous {expect[1]}, got {r.reason} {r.clarification}"
    ok = r.reason in ("not_understood", "nothing_documented") and not r.clarification
    return ok, "" if ok else f"expected a refusal, got {r.reason} {r.clarification}"


def evaluate() -> dict:
    d = tempfile.mkdtemp(prefix="sk_understand_eval_")
    b, users = build(os.path.join(d, "eval.db"))
    rows, wrong_answers, fuzzy_answers, leaks = [], [], [], []
    try:
        cases = [(q, "ana", e, t[0]) for t in TARGETS for q, e in variants(t)] + \
                [(q, a, e, "probe") for q, a, e in PROBES] + \
                [(q, a, e, "heldout") for q, a, e in HELDOUT]
        for q, asker, expect, tid in cases:
            r = b.respond(q, viewer=users[asker])
            ok, why = check(r, expect)
            rows.append({"q": q, "asker": asker, "target": tid, "expect": expect, "ok": ok,
                         "why": why, "sentence": r.sentence})
            if r.value is not None and expect[0] != "answer":
                (fuzzy_answers if expect[0] == "did_you_mean" else wrong_answers).append(rows[-1])
            elif r.value is not None and not ok:
                wrong_answers.append(rows[-1])
            blob = repr((r.sentence, r.value, r.clarification, r.understood_as, r.interpretation,
                         r.possible, [c.text for c in r.citations], r.steps))
            for owner, words in SECRETS.items():
                if owner != asker:
                    for w in words:                  # (the asker's own words are not a leak)
                        if w.lower() in blob.lower() and w.lower() not in q.lower():
                            leaks.append((q, asker, w))
        lang_rows = []
        for q, want in LANGUAGE:
            got = b.respond(q, viewer=users["ana"]).language
            lang_rows.append((q, want, got))
    finally:
        b.close()
        shutil.rmtree(d, ignore_errors=True)
    return {"rows": rows, "wrong_answers": wrong_answers, "fuzzy_answers": fuzzy_answers,
            "leaks": leaks, "language": lang_rows}


def report(res: dict) -> str:
    rows = res["rows"]
    var = [r for r in rows if r["target"] not in ("probe", "heldout")]
    probes = [r for r in rows if r["target"] == "probe"]
    held = [r for r in rows if r["target"] == "heldout"]
    out = [f"Dataset {DATASET_ID}: {len(TARGETS)} targets, {len(var)} variants, "
           f"{len(probes)} probes",
           f"  variants as expected: {sum(r['ok'] for r in var)}/{len(var)}",
           f"  probes as expected:   {sum(r['ok'] for r in probes)}/{len(probes)}",
           f"  held-out as expected: {sum(r['ok'] for r in held)}/{len(held)} (ideal "
           "expectations; a miss is a refusal unless listed as WRONG)",
           f"  WRONG answers: {len(res['wrong_answers'])}   answers from a misspelt name: "
           f"{len(res['fuzzy_answers'])}   private names leaked: {len(res['leaks'])}"]
    for lang in ("en", "es"):
        lr = [x for x in res["language"] if x[1] == lang]
        out.append(f"  language {lang}: {sum(1 for x in lr if x[2] == lang)}/{len(lr)}")
    by = {}
    for r in var:
        by.setdefault(r["target"], []).append(r["ok"])
    out.append("  per target: " + ", ".join(f"{t} {sum(v)}/{len(v)}" for t, v in by.items()))
    for r in rows:
        if not r["ok"]:
            out.append(f"  MISS {r['q']!r} ({r['asker']}): {r['why']}")
    for q, want, got in res["language"]:
        if want != got:
            out.append(f"  LANGUAGE {q!r}: expected {want}, got {got}")
    for leak in res["leaks"]:
        out.append(f"  LEAK {leak}")
    return "\n".join(out)


if __name__ == "__main__":
    print(report(evaluate()))
