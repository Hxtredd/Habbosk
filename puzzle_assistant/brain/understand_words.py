"""Words SK uses to understand a question (B5.1) - data, not sentence patterns.

Every word is written normalised (lower case, no accents), as text.normalize
produces it. Nothing here is specific to one sentence order: understand.py
only asks "which words are in the question?".

  CLUES       relation -> words that ask for it (strong) and words that only
              hint at it (weak: used when no strong word is present)
  PHRASES     two-word clues ("look after", "in charge") -> relations; a
              phrase may stand for more than one relation (then SK asks)
  QWORDS      question words -> their kind (who / what / where / when / how)
  COMPATIBLE  which kinds of question word fit a relation ("when" never asks
              who looks after a place)
  FILLER      words that carry no meaning of their own here
  POLITE      politeness / framing words, ignored ("please", "dime", "oye")
  YESNO       a question STARTING with one of these and having no question
              word is a yes / no question - left for a later milestone
  ROOM        words for "room" (a reverse question asks for a room)
  ES / EN     words that show the language of a question

Relations added as data (B5) get their clue words from their own question
forms and labels (understand.RelationVocab) - no code.
"""
from __future__ import annotations

CLUES = {
    "caretaker": (
        {"maintain", "maintains", "maintaining", "maintainer", "maintainers",
         "maintenance", "caretaker", "caretakers", "keeper", "keepers", "custodian", "warden",
         "care", "cares", "caring",
         "mantiene", "mantienen", "mantener", "mantenimiento", "cuida", "cuidan", "cuidar",
         "cuidador", "cuidadora", "cuidadores", "encargado", "encargada", "encargados",
         "encargadas", "encarga"},
        set()),
    "runs": (
        {"run", "runs", "running", "runner", "manage", "manages", "manager",
         "managers", "dirige", "dirigen", "dirigir", "director", "directora", "gestiona",
         "gestionan", "gerente", "administra", "administrador", "administradora", "regenta"},
        set()),
    "room_id": (
        {"id", "ids", "identifier", "code", "codigo", "identificador"},
        {"number", "numero", "num"}),
    "quest_reward": (
        {"coins", "coin", "monedas", "moneda", "reward", "rewards", "recompensa", "recompensas",
         "quest", "quests", "mision", "misiones"},
        {"give", "gives", "pay", "pays", "da", "dan", "paga", "pagan"}),
    "leads_to": (
        {"lead", "leads", "leading", "exit", "exits", "door", "doors", "gate", "gates", "path",
         "paths", "stairs", "staircase", "archway", "arch", "connects", "connect", "salida",
         "salidas", "puerta", "puertas", "camino", "caminos", "escalera", "escaleras", "arco",
         "arcos", "conduce", "conducen", "lleva", "llevan", "conecta"},
        {"go", "goes", "va", "van"}),
}

# two words that ask together; one phrase may stand for several relations
PHRASES = {
    ("look", "after"): ("caretaker",), ("looks", "after"): ("caretaker",),
    ("looking", "after"): ("caretaker",), ("looked", "after"): ("caretaker",),
    ("take", "care"): ("caretaker",), ("takes", "care"): ("caretaker",),
    ("taking", "care"): ("caretaker",),
    ("in", "charge"): ("caretaker", "runs"), ("a", "cargo"): ("caretaker", "runs"),
    ("responsible", "for"): ("caretaker", "runs"), ("responsable", "de"): ("caretaker", "runs"),
    ("responsable", "del"): ("caretaker", "runs"),
}

QWORDS = {
    "who": "who", "whom": "who", "whose": "who", "quien": "who", "quienes": "who",
    "what": "what", "which": "what", "que": "what", "cual": "what", "cuales": "what",
    "where": "where", "donde": "where", "adonde": "where",
    "when": "when", "cuando": "when",
    "how": "how", "many": "how", "much": "how", "cuanto": "how", "cuanta": "how",
    "cuantos": "how", "cuantas": "how",
}

# question-word kinds a relation may be asked with (place questions)
COMPATIBLE = {
    "caretaker": {"who", "what"},
    "runs": {"who", "what"},
    "room_id": {"what"},
    "quest_reward": {"how", "what"},
    "leads_to": {"where", "what"},
}
# reverse questions ("which room does Mira look after?")
COMPATIBLE_INVERSE = {"what", "where"}
INVERSE_RELATIONS = ("caretaker", "runs")

FILLER = {
    # English
    "the", "a", "an", "of", "in", "at", "on", "for", "from", "to", "into", "by", "with", "its",
    "it", "is", "s", "are", "does", "do", "has", "have", "there", "this", "that",
    "please", "be", "currently", "now", "called", "named", "by", "he", "she", "they", "her",
    "his", "their",
    # Spanish
    "el", "la", "los", "las", "lo", "le", "un", "una", "de", "del", "al", "en", "por", "para",
    "con", "es", "son", "esta", "estan", "su", "sus", "se", "tiene", "tienen", "hay", "actualmente",
    "ahora", "llama", "llamada", "llamado",
}

POLITE = {
    "please", "pls", "plz", "tell", "me", "you", "can", "could", "would", "i", "want", "know",
    "like", "d", "hey", "hi", "hello", "sk", "thanks", "thank", "and", "so", "ok", "okay",
    "also", "again", "anyone", "somebody", "someone",
    "favor", "dime", "dices", "decir", "decirme", "sabes", "sabe", "sabeis", "oye", "hola",
    "quiero", "saber", "puedes", "puede", "podrias", "podria", "gracias", "y", "pues",
    "entonces", "tambien", "vale", "bueno", "porfa", "porfavor",
}
# "me" and "you" are polite only around a request ("tell me", "can you"):
# kept simple - they never carry a fact either way.

# the past or the future: SK only knows what is recorded as current
TIME = {"was", "were", "did", "used", "before", "previously", "formerly", "earlier", "ago",
        "yesterday", "will", "tomorrow", "next", "later", "soon", "maintained", "managed",
        "looked", "ran", "led", "gave", "paid", "opened", "era", "eran", "fue", "fueron",
        "solia", "solian", "antes", "ayer", "sera", "seran", "proximo", "proxima", "pronto",
        "luego", "despues", "manana", "cared", "kept", "went", "had",
        # Spanish past / future forms of the clue verbs (accents removed)
        "cuidaba", "cuidaban", "cuidara", "cuidaran", "cuidaria", "mantenia", "mantenian",
        "mantuvo", "mantendra", "mantendran", "mantendria", "dirigia", "dirigio", "dirigira",
        "dirigiran", "gestionaba", "gestiono", "gestionara", "llevaba", "llevara", "llevo",
        "conducia", "conducira", "abria", "abrio", "abrira", "daba", "dio", "dara", "daran",
        "pagaba", "pago", "pagara", "encargaba", "encargara", "tenia", "tuvo", "tendra",
        "habia", "habra", "estaba", "estuvo", "estara"}

# past participles: a present passive ("is maintained by") asks about now
PARTICIPLES = {"maintained": "caretaker", "managed": "runs", "looked": "caretaker"}

YESNO = {"does", "do", "did", "is", "are", "was", "were", "has", "have", "can", "could",
         "tiene", "tienen", "hay", "existe", "es", "esta"}

ROOM = {"room", "rooms", "place", "places", "sala", "salas", "habitacion", "habitaciones",
        "lugar", "lugares", "sitio"}

# a leads_to question with one of these just before the place asks what leads
# INTO it ("what connects to the X?") - the other direction: refused
INTO = {"to", "into", "towards", "toward", "reach", "reaches", "a", "al", "hacia", "hasta"}

# language signs (accents removed); words inside a known name are not counted.
# Spanish articles, prepositions and directions count half: they are common
# inside place names ("Faro del Norte room ID").
ES_WEAK = {"del", "el", "la", "los", "las", "lo", "de", "al", "en", "por", "su", "y", "un",
           "una", "norte", "sur", "este", "oeste", "sala"}
ES = ES_WEAK | {"que", "quien", "quienes", "cual", "cuales", "cuando", "donde", "adonde", "cuanto",
      "cuanta", "cuantos", "cuantas", "como", "es", "esta", "son", "tiene", "tienen", "hay",
      "lleva", "cuida", "mantiene", "dirige", "abre", "salida", "puerta", "mision",
      "monedas", "encargado", "encargada", "favor", "dime", "sabes", "oye", "hola", "numero",
      "codigo", "hora", "quiero", "saber", "puedes", "da",
      "cuidador", "cuidadora", "camino", "recompensa"}
EN = {"who", "what", "when", "where", "which", "how", "many", "much", "does", "do", "did",
      "is", "are", "the", "of", "to", "in", "at", "maintains", "maintain", "runs", "run",
      "room", "exit", "door", "gate", "quest", "coins", "reward", "lead", "leads", "go", "goes",
      "please", "tell", "me", "know", "open", "opens", "look", "looks", "after", "care",
      "caretaker", "north", "south", "east", "west", "number", "give", "gives", "s", "you",
      "can", "keeper", "manages", "path"}

# question words in both languages (to tell a question from a plain list of words)
QUESTION_KINDS = ("who", "what", "where", "when", "how")
