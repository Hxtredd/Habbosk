"""B6 false-quotation corpus (synthetic). Expectations written from the rules, not from code."""

ZW = "​"      # zero-width space
BIDI = "‮"    # right-to-left override
CA = "а"      # Cyrillic a
CO = "о"      # Cyrillic o
CE = "е"      # Cyrillic e

_C = []


def _c(id, cat, claim, subj, src, quote=None, kind="sentence", author="named", cur=True, exp=False):
    """Tiny helper: quote defaults to the whole source text."""
    _C.append((id, cat, claim, subj, src, src if quote is None else quote, kind, author, cur, exp))


# claims
MG = ("Mira maintains the Lantern Garden.", "Lantern Garden")
LR = ("Lio runs the Rope Shed.", "Rope Shed")
GL = ("The Glass Library has room ID 42.", "Glass Library")
CT = ("The quest in the Clock Tower gives 50 coins.", "Clock Tower")
HS = ("The north door of the Harbour Square leads to the Old Pier.", "Harbour Square")
RS = ("The Rope Shed has no caretaker.", "Rope Shed")
FN = ("El encargado del Faro Norte es Bruno.", "Faro Norte")
MA = ("El Mercado Azul tiene el ID 17.", "Mercado Azul")
FS = ("La misión del Faro Sur da 30 monedas.", "Faro Sur")
PP = ("La salida este del Mercado Azul lleva al Faro Sur.", "Mercado Azul")
PE = ("Faro Norte no tiene encargado.", "Faro Norte")
NC = ("Nora looks after the Glass Library.", "Glass Library")
CI = ("Captain Ines is the caretaker of the Old Pier.", "Old Pier")
DR = ("Don Ramón maintains the Plaza del Puerto.", "Plaza del Puerto")

# ---------------- exact_ok (True) ----------------
_c("ok01", "exact_ok", *MG, "Mira maintains the Lantern Garden.", exp=True)
_c("ok02", "exact_ok", *MG, "Mira maintains the Lantern Garden.", quote="Mira maintains the Lantern Garden", exp=True)
_c("ok03", "exact_ok", *MG, "Mira looks after the Lantern Garden.", exp=True)
_c("ok04", "exact_ok", *MG, "Mira is the caretaker of the Lantern Garden.", exp=True)
_c("ok05", "exact_ok", *LR, "Lio runs the Rope Shed.", exp=True)
_c("ok06", "exact_ok", *LR, "Lio runs the Rope Shed.", quote="runs the Rope Shed", exp=True)
_c("ok07", "exact_ok", *GL, "The Glass Library has room ID 42.", exp=True)
_c("ok08", "exact_ok", *CT, "The quest in the Clock Tower gives 50 coins.", exp=True)
_c("ok09", "exact_ok", *HS, "The north door of the Harbour Square leads to the Old Pier.", exp=True)
_c("ok10", "exact_ok", *HS, "The north gate of the Harbour Square leads to the Old Pier.", exp=True)
_c("ok11", "exact_ok", *HS, "The north exit of the Harbour Square leads to the Old Pier.", exp=True)
_c("ok12", "exact_ok", *RS, "The Rope Shed has no caretaker.", exp=True)
_c("ok13", "exact_ok", *NC, "Nora looks after the Glass Library.", exp=True)
_c("ok14", "exact_ok", *CI, "Captain Ines is the caretaker of the Old Pier.", exp=True)
_c("ok15", "exact_ok", *DR, "Don Ramón maintains the Plaza del Puerto.", exp=True)
_c("ok16", "exact_ok", *MG, "The harbour is quiet today. Mira maintains the Lantern Garden.", quote="Mira maintains the Lantern Garden.", exp=True)
_c("ok17", "exact_ok", *MG, "Mira maintains the Lantern Garden. The harbour is quiet today.", quote="Mira maintains the Lantern Garden.", exp=True)
_c("ok18", "exact_ok", *GL, "The Glass Library has room ID 42.", quote="has room ID 42", exp=True)
# list items
_c("li01", "exact_ok", *MG, "Mira maintains the Lantern Garden.", kind="list_item", exp=True)
_c("li02", "exact_ok", *LR, "Lio runs the Rope Shed.", kind="list_item", exp=True)
_c("li03", "exact_ok", *CT, "The quest in the Clock Tower gives 50 coins.", kind="list_item", exp=True)
_c("li04", "exact_ok", *GL, "The Glass Library has room ID 42.", kind="list_item", exp=True)
_c("li05", "exact_ok", *MG, "Mira maintains the Lantern Garden.", quote="maintains the Lantern Garden", kind="list_item", exp=True)

# ---------------- unknown_source_ok (True) ----------------
_c("un01", "unknown_source_ok", *MG, "Mira maintains the Lantern Garden.", author="unknown", exp=True)
_c("un02", "unknown_source_ok", *LR, "Lio runs the Rope Shed.", author="unknown", exp=True)
_c("un03", "unknown_source_ok", *GL, "The Glass Library has room ID 42.", author="unknown", exp=True)
_c("un04", "unknown_source_ok", *FN, "El encargado del Faro Norte es Bruno.", author="unknown", exp=True)
_c("un05", "unknown_source_ok", *HS, "The north door of the Harbour Square leads to the Old Pier.", author="unknown", kind="list_item", exp=True)

# ---------------- spanish_ok (True) ----------------
_c("es01", "spanish_ok", *FN, "El encargado del Faro Norte es Bruno.", exp=True)
_c("es02", "spanish_ok", *PE, "Faro Norte no tiene encargado.", exp=True)
_c("es03", "spanish_ok", *MA, "El Mercado Azul tiene el ID 17.", exp=True)
_c("es04", "spanish_ok", *FS, "La misión del Faro Sur da 30 monedas.", exp=True)
_c("es05", "spanish_ok", *PP, "La salida este del Mercado Azul lleva al Faro Sur.", exp=True)
_c("es06", "spanish_ok", *FN, "El encargado del Faro Norte es Bruno.", quote="El encargado del Faro Norte es Bruno", exp=True)
_c("es07", "spanish_ok", *FS, "El puerto despierta temprano. La misión del Faro Sur da 30 monedas.", quote="La misión del Faro Sur da 30 monedas.", exp=True)
_c("es08", "spanish_ok", *MA, "El Mercado Azul tiene el ID 17.", kind="list_item", exp=True)
_c("es09", "spanish_ok", "El encargado del Mercado Azul es Tomás.", "Mercado Azul", "El encargado del Mercado Azul es Tomás.", exp=True)
_c("es10", "spanish_ok", "La misión del Faro Sur da 30 monedas.", "Faro Sur", "La misión del Faro Sur da 30 monedas.", author="unknown", exp=True)

# ---------------- wrong_value ----------------
_c("wv01", "wrong_value", *GL, "The Glass Library has room ID 43.")
_c("wv02", "wrong_value", *GL, "The Glass Library has room ID 420.")
_c("wv03", "wrong_value", *CT, "The quest in the Clock Tower gives 15 coins.")
_c("wv04", "wrong_value", *CT, "The quest in the Clock Tower gives 500 coins.")
_c("wv05", "wrong_value", *MA, "El Mercado Azul tiene el ID 71.")
_c("wv06", "wrong_value", *FS, "La misión del Faro Sur da 3 monedas.")

# ---------------- wrong_person ----------------
_c("wp01", "wrong_person", *MG, "Lio maintains the Lantern Garden.")
_c("wp02", "wrong_person", *LR, "Nora runs the Rope Shed.")
_c("wp03", "wrong_person", *CI, "Bruno is the caretaker of the Old Pier.")
_c("wp04", "wrong_person", *FN, "El encargado del Faro Norte es Carla.")
_c("wp05", "wrong_person", *NC, "Carla looks after the Glass Library.")

# ---------------- wrong_place ----------------
_c("wl01", "wrong_place", *MG, "Mira maintains the Rope Shed.")
_c("wl02", "wrong_place", *GL, "The Clock Tower has room ID 42.")
_c("wl03", "wrong_place", *CT, "The quest in the Glass Library gives 50 coins.")
_c("wl04", "wrong_place", *FN, "El encargado del Faro Sur es Bruno.")
_c("wl05", "wrong_place", *LR, "Lio runs the Lantern Garden.")
_c("wl06", "wrong_place", *HS, "The north door of the Harbour Square leads to the Rope Shed.")

# ---------------- wrong_direction ----------------
_c("wd01", "wrong_direction", *HS, "The south door of the Harbour Square leads to the Old Pier.")
_c("wd02", "wrong_direction", *HS, "The east gate of the Harbour Square leads to the Old Pier.")
_c("wd03", "wrong_direction", *PP, "La salida oeste del Mercado Azul lleva al Faro Sur.")
_c("wd04", "wrong_direction", *PP, "La salida norte del Mercado Azul lleva al Faro Sur.")

# ---------------- other_relation ----------------
_c("or01", "other_relation", *MG, "Mira runs the Lantern Garden.")
_c("or02", "other_relation", *LR, "Lio maintains the Rope Shed.")
_c("or03", "other_relation", *LR, "Lio looks after the Rope Shed.")
_c("or04", "other_relation", *GL, "The Glass Library has a quest.", )
_c("or05", "other_relation", *RS, "Lio is the caretaker of the Rope Shed.")
_c("or06", "other_relation", *CT, "The Clock Tower has room ID 50.")
_c("or07", "other_relation", *HS, "The north door of the Harbour Square has room ID 9.")

# ---------------- negation ----------------
_c("ng01", "negation", *MG, "Mira does not maintain the Lantern Garden.")
_c("ng02", "negation", *MG, "It is not true that Mira maintains the Lantern Garden.")
_c("ng03", "negation", *LR, "Lio never runs the Rope Shed.")
_c("ng04", "negation", *GL, "The Glass Library doesn't have room ID 42.")
_c("ng05", "negation", *FN, "El encargado del Faro Norte no es Bruno.")
_c("ng06", "negation", *FS, "La misión del Faro Sur nunca da 30 monedas.")
_c("ng07", "negation", *CT, "The quest in the Clock Tower does not give 50 coins.")
_c("ng08", "negation", *RS, "The Rope Shed isn't without a caretaker.")

# ---------------- hedge ----------------
_c("hg01", "hedge", *MG, "Maybe Mira maintains the Lantern Garden.")
_c("hg02", "hedge", *LR, "Perhaps Lio runs the Rope Shed.")
_c("hg03", "hedge", *GL, "The Glass Library probably has room ID 42.")
_c("hg04", "hedge", *CT, "The quest in the Clock Tower might give 50 coins.")
_c("hg05", "hedge", *FN, "Quizás el encargado del Faro Norte es Bruno.")
_c("hg06", "hedge", *MA, "Creo que el Mercado Azul tiene el ID 17.")
_c("hg07", "hedge", *MG, "I think Mira maintains the Lantern Garden.")
_c("hg08", "hedge", *LR, "It seems Lio runs the Rope Shed.")

# ---------------- condition ----------------
_c("cd01", "condition", *MG, "On weekends, Mira maintains the Lantern Garden.")
_c("cd02", "condition", *LR, "In the old version, Lio runs the Rope Shed.")
_c("cd03", "condition", *GL, "If the tide is high, the Glass Library has room ID 42.")
_c("cd04", "condition", *CT, "When it rains, the quest in the Clock Tower gives 50 coins.")
_c("cd05", "condition", *MG, "Mira maintains the Lantern Garden during the festival.")
_c("cd06", "condition", *LR, "Lio runs the Rope Shed every morning.")
_c("cd07", "condition", *FN, "Los domingos, el encargado del Faro Norte es Bruno.")

# ---------------- past_future ----------------
_c("pf01", "past_future", *MG, "Mira used to maintain the Lantern Garden.")
_c("pf02", "past_future", *LR, "Lio formerly runs the Rope Shed.")
_c("pf03", "past_future", *MG, "Mira will maintain the Lantern Garden.")
_c("pf04", "past_future", *GL, "The Glass Library previously has room ID 42.")
_c("pf05", "past_future", *FN, "Antes, el encargado del Faro Norte es Bruno.")
_c("pf06", "past_future", *FN, "El encargado del Faro Norte será Bruno.")
_c("pf07", "past_future", *LR, "Soon Lio runs the Rope Shed.")
_c("pf08", "past_future", *MG, "Mira maintains the Lantern Garden until June.")

# ---------------- reported ----------------
_c("rp01", "reported", *MG, "Lio says that Mira maintains the Lantern Garden.")
_c("rp02", "reported", *LR, "According to Nora, Lio runs the Rope Shed.")
_c("rp03", "reported", *GL, "Bruno claims that the Glass Library has room ID 42.")
_c("rp04", "reported", *FN, "Carla dice que el encargado del Faro Norte es Bruno.")
_c("rp05", "reported", *CT, "Captain Ines says the quest in the Clock Tower gives 50 coins.")

# ---------------- question ----------------
_c("qs01", "question", *MG, "Does Mira maintain the Lantern Garden?")
_c("qs02", "question", *LR, "Who runs the Rope Shed? Lio runs the Rope Shed?")
_c("qs03", "question", *FN, "¿El encargado del Faro Norte es Bruno?")
_c("qs04", "question", *GL, "Has the Glass Library room ID 42?")

# ---------------- run_on (two facts joined) ----------------
_c("ro01", "run_on", *MG, "Mira maintains the Lantern Garden and the Rope Shed.")
_c("ro02", "run_on", *MG, "Mira maintains the Lantern Garden and Lio runs the Rope Shed.")
_c("ro03", "run_on", *LR, "Lio runs the Rope Shed and the Glass Library.")
_c("ro04", "run_on", *FN, "El encargado del Faro Norte es Bruno y Carla.")
_c("ro05", "run_on", *GL, "The Glass Library has room ID 42 and the Clock Tower has room ID 43.")
_c("ro06", "run_on", *LR, "Mira maintains the Lantern Garden and Lio runs the Rope Shed.", quote="Lio runs the Rope Shed")

# ---------------- pronoun_context ----------------
_c("pr01", "pronoun_context", *MG, "She maintains it.")
_c("pr02", "pronoun_context", *GL, "It has room ID 42.")
_c("pr03", "pronoun_context", *LR, "He runs the place.")
_c("pr04", "pronoun_context", *FN, "El encargado de este lugar es Bruno.")
_c("pr05", "pronoun_context", *MG, "Mira maintains it.")
_c("pr06", "pronoun_context", *CT, "The quest there gives 50 coins.")

# ---------------- lowercase_name ----------------
_c("lc01", "lowercase_name", *MG, "Mira maintains the garden.")
_c("lc02", "lowercase_name", *MG, "Mira maintains the lantern garden.")
_c("lc03", "lowercase_name", *LR, "Lio runs the rope shed.")
_c("lc04", "lowercase_name", *GL, "The glass library has room ID 42.")
_c("lc05", "lowercase_name", *FN, "El encargado del faro norte es Bruno.")
_c("lc06", "lowercase_name", *MG, "mira maintains the Lantern Garden.")

# ---------------- partial_of_negated ----------------
_c("pn01", "partial_of_negated", *MG, "It is not true that Mira maintains the Lantern Garden.", quote="Mira maintains the Lantern Garden")
_c("pn02", "partial_of_negated", *LR, "Lio does not run the Rope Shed, Nora runs the Rope Shed.", quote="Nora runs the Rope Shed")
_c("pn03", "partial_of_negated", *GL, "The Glass Library does not have room ID 42.", quote="the Glass Library")
_c("pn04", "partial_of_negated", *FN, "No es cierto que el encargado del Faro Norte es Bruno.", quote="el encargado del Faro Norte es Bruno")
_c("pn05", "partial_of_negated", *CT, "The quest in the Clock Tower never gives 50 coins.", quote="gives 50 coins")

# ---------------- partial_of_hedged ----------------
_c("ph01", "partial_of_hedged", *MG, "Maybe Mira maintains the Lantern Garden.", quote="Mira maintains the Lantern Garden")
_c("ph02", "partial_of_hedged", *LR, "I believe that Lio runs the Rope Shed.", quote="Lio runs the Rope Shed")
_c("ph03", "partial_of_hedged", *GL, "The Glass Library may have room ID 42.", quote="have room ID 42")
_c("ph04", "partial_of_hedged", *FN, "Tal vez el encargado del Faro Norte es Bruno.", quote="el encargado del Faro Norte es Bruno")
_c("ph05", "partial_of_hedged", *CT, "On weekends the quest in the Clock Tower gives 50 coins.", quote="the quest in the Clock Tower gives 50 coins")

# ---------------- not_in_source ----------------
_c("ns01", "not_in_source", *MG, "Lio maintains the Rope Shed.", quote="Mira maintains the Lantern Garden.")
_c("ns02", "not_in_source", *LR, "The harbour wakes early.", quote="Lio runs the Rope Shed.")
_c("ns03", "not_in_source", *GL, "The Glass Library is open.", quote="The Glass Library has room ID 42.")
_c("ns04", "not_in_source", *FN, "El puerto despierta temprano.", quote="El encargado del Faro Norte es Bruno.")
_c("ns05", "not_in_source", *MG, "Mira maintains the Lantern Garden.", quote="Mira maintains the Lantern Garden daily.")
_c("ns06", "not_in_source", *MG, "Mira maintains the Lantern Garden.", quote="Mira looks after the Lantern Garden.")

# ---------------- near_miss_char ----------------
_c("nc01", "near_miss_char", *MG, "Mira maintains the Lantern Garden.", quote="Mira maintain the Lantern Garden.")
_c("nc02", "near_miss_char", *MG, "Mira maintains the Lantern Garden.", quote="Mira maintains the Lantern Gardn.")
_c("nc03", "near_miss_char", *LR, "Lio runs the Rope Shed.", quote="Lio run the Rope Shed.")
_c("nc04", "near_miss_char", *MG, "Mira maintains the Lantern Garden.", quote="Mira maintains a Lantern Garden.")
_c("nc05", "near_miss_char", *FN, "El encargado del Faro Norte es Bruno.", quote="El encargada del Faro Norte es Bruno.")
_c("nc06", "near_miss_char", *LR, "Lio runs the Rope Shed.", quote="Lio runs the Rope Shed!")

# ---------------- near_miss_number ----------------
_c("nn01", "near_miss_number", *GL, "The Glass Library has room ID 42.", quote="The Glass Library has room ID 24.")
_c("nn02", "near_miss_number", *GL, "The Glass Library has room ID 42.", quote="The Glass Library has room ID 4.")
_c("nn03", "near_miss_number", *CT, "The quest in the Clock Tower gives 50 coins.", quote="The quest in the Clock Tower gives 5 coins.")
_c("nn04", "near_miss_number", *MA, "El Mercado Azul tiene el ID 17.", quote="El Mercado Azul tiene el ID 170.")
_c("nn05", "near_miss_number", *FS, "La misión del Faro Sur da 30 monedas.", quote="La misión del Faro Sur da 31 monedas.")

# ---------------- accent_changed ----------------
_c("ac01", "accent_changed", *FS, "La misión del Faro Sur da 30 monedas.", quote="La mision del Faro Sur da 30 monedas.")
_c("ac02", "accent_changed", "Tomás maintains the Clock Tower.", "Clock Tower", "Tomás maintains the Clock Tower.", quote="Tomas maintains the Clock Tower.")
_c("ac03", "accent_changed", *DR, "Don Ramón maintains the Plaza del Puerto.", quote="Don Ramon maintains the Plaza del Puerto.")
_c("ac04", "accent_changed", "Tomás maintains the Clock Tower.", "Clock Tower", "Tomas maintains the Clock Tower.", quote="Tomás maintains the Clock Tower.")
# ac05 corrected (B6 review): the approved cleaning is NFC, so a DECOMPOSED accent in
# the quote is the same text after cleaning - genuine support, not a changed accent.
_c("ac05", "nfc_ok", *FS, "La misión del Faro Sur da 30 monedas.", quote="La misio\u0301n del Faro Sur da 30 monedas.", exp=True)

# ---------------- case_changed ----------------
_c("cc01", "case_changed", *MG, "Mira maintains the Lantern Garden.", quote="mira maintains the Lantern Garden.")
_c("cc02", "case_changed", *MG, "Mira maintains the Lantern Garden.", quote="Mira maintains the lantern garden.")
_c("cc03", "case_changed", *LR, "Lio runs the Rope Shed.", quote="LIO RUNS THE ROPE SHED.")
_c("cc04", "case_changed", *GL, "The Glass Library has room ID 42.", quote="The Glass Library has room id 42.")
_c("cc05", "case_changed", *FN, "El encargado del Faro Norte es Bruno.", quote="el encargado del Faro Norte es Bruno.")

# ---------------- whitespace_changed ----------------
_c("ws01", "whitespace_changed", *MG, "Mira maintains the Lantern Garden.", quote="Mira  maintains the Lantern Garden.")
_c("ws02", "whitespace_changed", *LR, "Lio runs the Rope Shed.", quote="Lio runs the RopeShed.")
_c("ws03", "whitespace_changed", *GL, "The Glass Library has room ID 42.", quote="The Glass Library has room ID  42.")
_c("ws04", "whitespace_changed", *MG, "Mira maintains the Lantern Garden.", quote=" Mira maintains the Lantern Garden. ")
_c("ws05", "whitespace_changed", *LR, "Lio runs the Rope Shed.", quote="Lio runs the\tRope Shed.")
_c("ws06", "whitespace_changed", *LR, "Lio runs\nthe Rope Shed.", quote="Lio runs the Rope Shed.")

# ---------------- quote_char_changed ----------------
_c("qc01", "quote_char_changed", *MG, "Mira maintains the “Lantern Garden”.", quote='Mira maintains the "Lantern Garden".')
_c("qc02", "quote_char_changed", *MG, 'Mira maintains the "Lantern Garden".', quote="Mira maintains the “Lantern Garden”.")
_c("qc03", "quote_char_changed", "Nora maintains the Captain’s Landing.", "Captain's Landing", "Nora maintains the Captain’s Landing.", quote="Nora maintains the Captain's Landing.")
_c("qc04", "quote_char_changed", "Nora maintains the Captain's Landing.", "Captain's Landing", "Nora maintains the Captain's Landing.", quote="Nora maintains the Captain’s Landing.")

# ---------------- spans_passages ----------------
_c("sp01", "spans_passages", *MG, "Mira maintains the Lantern Garden. Lio runs the Rope Shed.", quote="Garden. Lio runs")
_c("sp02", "spans_passages", *LR, "The harbour is calm. Lio runs the Rope Shed.", quote="calm. Lio runs the Rope Shed.")
_c("sp03", "spans_passages", *GL, "The Glass Library is old. It has room ID 42.", quote="old. It has room ID 42.")
_c("sp04", "spans_passages", *FN, "El Faro Norte es alto. El encargado del Faro Norte es Bruno.", quote="alto. El encargado del Faro Norte es Bruno.")
_c("sp05", "spans_passages", *MG, "Mira maintains the Lantern Garden. Lio runs the Rope Shed.", quote="Mira maintains the Lantern Garden. Lio runs the Rope Shed.")
_c("sp06", "spans_passages", *CT, "Nora walks fast! The quest in the Clock Tower gives 50 coins.", quote="fast! The quest in the Clock Tower gives 50 coins.")

# ---------------- table_row / heading / code / blockquote ----------------
_c("tr01", "table_row", *MG, "| Lantern Garden | Mira maintains the Lantern Garden. |", kind="table_row")
_c("tr02", "table_row", *LR, "Lio runs the Rope Shed.", kind="table_row")
_c("tr03", "table_row", *GL, "The Glass Library has room ID 42.", kind="table_row")
_c("tr04", "table_row", *FN, "El encargado del Faro Norte es Bruno.", kind="table_row")
_c("hd01", "heading", *MG, "Mira maintains the Lantern Garden.", kind="heading")
_c("hd02", "heading", *LR, "Lio runs the Rope Shed.", kind="heading")
_c("hd03", "heading", *CT, "The quest in the Clock Tower gives 50 coins.", kind="heading")
_c("hd04", "heading", *FN, "El encargado del Faro Norte es Bruno.", kind="heading")
_c("cb01", "code", *MG, "Mira maintains the Lantern Garden.", kind="code")
_c("cb02", "code", *GL, "The Glass Library has room ID 42.", kind="code")
_c("cb03", "code", *LR, "Lio runs the Rope Shed.", kind="code")
_c("cb04", "code", *MA, "El Mercado Azul tiene el ID 17.", kind="code")
_c("bq01", "blockquote", *MG, "Mira maintains the Lantern Garden.", kind="quote")
_c("bq02", "blockquote", *LR, "Lio runs the Rope Shed.", kind="quote")
_c("bq03", "blockquote", *HS, "The north door of the Harbour Square leads to the Old Pier.", kind="quote")
_c("bq04", "blockquote", *FS, "La misión del Faro Sur da 30 monedas.", kind="quote")

# ---------------- ai_source / self_source ----------------
_c("ai01", "ai_source", *MG, "Mira maintains the Lantern Garden.", author="ai_generated")
_c("ai02", "ai_source", *LR, "Lio runs the Rope Shed.", author="ai_generated")
_c("ai03", "ai_source", *GL, "The Glass Library has room ID 42.", author="ai_generated", kind="list_item")
_c("ai04", "ai_source", *FN, "El encargado del Faro Norte es Bruno.", author="ai_generated")
_c("se01", "self_source", *MG, "Mira maintains the Lantern Garden.", author="self")
_c("se02", "self_source", *CT, "The quest in the Clock Tower gives 50 coins.", author="self")
_c("se03", "self_source", *LR, "Lio runs the Rope Shed.", author="self", kind="list_item")
_c("se04", "self_source", *MA, "El Mercado Azul tiene el ID 17.", author="self")

# ---------------- suspicious_chars ----------------
_c("sc01", "suspicious_chars", *MG, "Mira maintains the Lantern" + ZW + " Garden.")
_c("sc02", "suspicious_chars", *MG, "Mira maintains the Lantern Garden." + ZW)
_c("sc03", "suspicious_chars", *LR, "Lio runs the " + BIDI + "Rope Shed.")
_c("sc04", "suspicious_chars", *GL, "The Glass Library has room ID " + ZW + "42.")
_c("sc05", "suspicious_chars", *FN, "El encargado del Faro Norte es Bruno." + BIDI)
_c("sc06", "suspicious_chars", *MG, "Mira maintains the Lantern" + ZW + " Garden.", quote="Mira maintains the")
_c("sc07", "suspicious_chars", *CT, "The quest in the Clock Tower gives 50 coins.﻿")

# ---------------- homoglyph ----------------
_c("hm01", "homoglyph", *MG, "Mira maintains the Lantern G" + CA + "rden.")
_c("hm02", "homoglyph", *LR, "Lio runs the R" + CO + "pe Shed.")
_c("hm03", "homoglyph", *MG, "Mira maintains the Lantern G" + CA + "rden.", quote="Mira maintains the Lantern G" + CA + "rden.")
_c("hm04", "homoglyph", *FN, "El encargado del Faro Norte es Bruno.".replace("Bruno", "Br" + "у" + "no"))
_c("hm05", "homoglyph", *LR, "Lio runs the Rop" + CE + " Shed.", quote="Lio runs the")
_c("hm06", "homoglyph", *GL, "The Gl" + CA + "ss Library has room ID 42.")
_c("hm07", "homoglyph", *MG, "Mira maintains the Lantern Γarden.")  # Greek capital gamma

# ---------------- old_version ----------------
_c("ov01", "old_version", *MG, "Mira maintains the Lantern Garden.", cur=False)
_c("ov02", "old_version", *LR, "Lio runs the Rope Shed.", cur=False)
_c("ov03", "old_version", *GL, "The Glass Library has room ID 42.", cur=False, kind="list_item")
_c("ov04", "old_version", *FN, "El encargado del Faro Norte es Bruno.", cur=False)
_c("ov05", "old_version", *CT, "The quest in the Clock Tower gives 50 coins.", cur=False, author="unknown")

# ---------------- spanish_bad ----------------
_c("sb01", "spanish_bad", *FN, "El encargado del Faro Norte no es Bruno.")
_c("sb02", "spanish_bad", *FN, "Tal vez el encargado del Faro Norte es Bruno.")
_c("sb03", "spanish_bad", *MA, "El Mercado Azul tiene el ID 18.")
_c("sb04", "spanish_bad", *FS, "La misión del Faro Sur solía dar 30 monedas.")
_c("sb05", "spanish_bad", *PP, "La salida este del Mercado Azul lleva al Faro Norte.")
_c("sb06", "spanish_bad", *PP, "La salida sur del Mercado Azul lleva al Faro Sur.")
_c("sb07", "spanish_bad", *FN, "Según Carla, el encargado del Faro Norte es Bruno.")
_c("sb08", "spanish_bad", *PE, "Faro Norte tiene encargado.")
_c("sb09", "spanish_bad", *FN, "El encargado del Faro Norte es Bruno cuando llueve.")
_c("sb10", "spanish_bad", *FN, "Parece que el encargado del Faro Norte es Bruno.")
_c("sb11", "spanish_bad", *FS, "La misión del Faro Sur podría dar 30 monedas.")

CASES = [x for x in _C if x is not None]
