"""Question understanding (B5.1): the meaning of a question, not its pattern.

    u = understand(question, names, vocab)

A question becomes a small structured meaning, whatever its word order:

    intent      a relation of a place ("who looks after X", "X's room ID")
                or its reverse ("which room does Mira look after?")
    relation    caretaker / runs / room_id / quest_reward / leads_to, or a
                relation added as data (B5)
    entity      ONE place (or, for a reverse question, one person) the asker
                may see - never a name from anyone else's private knowledge
    qualifier   a direction, for exits

It is used by respond() ONLY when B3's exact grammar did not understand the
question, or understood it but with a name the asker does not know. ask() and
search() are unchanged. The meaning goes to the same B3 reasoning, so every
B4 / B5 guarantee (categories, the strict support rule, citations, privacy)
applies unchanged.

How (rule-based, no AI, no network):
  1. words are normalised (capitals, accents, punctuation removed);
  2. names the asker may see are found exactly, longest first, anywhere;
  3. every other word must be explained: a relation clue (understand_words),
     a question word, a direction, a filler or politeness word;
  4. a word that is not explained may be a small typo of ONE question word
     (1 letter in words of 4-7 letters, 2 in longer words; never in words of
     3 letters or fewer, never in numbers) - the answer then says how the
     question was understood ("Understood as ...");
  5. with no exact name, a name with such typos is only SUGGESTED ("Did you
     mean ...?" - never answered, never evidence, never a silent change);
     several close names, or a partial name fitting several, stay ambiguous;
  6. refused (not understood, exactly as before) if anything is left
     unexplained, if it is negated, names two or more entities, asks for two
     relations at once about an exit (a chained question), is a yes / no
     question, or uses a question word that does not fit the relation
     ("when is the X maintained?").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import understand_words as W
from .facts import _DIRS
from .reason import Ask
from .text import NEGATIONS, _CONTRACTION_STEMS, normalize, stem, tokens


MAX_NAME_EDITS = 2          # letters that may differ in a whole name

# -- typo distance ----------------------------------------------------------------------
def bound(a: str, b: str) -> int:
    """How many letters may differ between a question word and a known word:
    none for words of 3 letters or fewer and for anything with a digit, 1 for
    4-7 letters, 2 for 8 or more."""
    n = min(len(a), len(b))
    if n <= 3 or not (a.isalpha() and b.isalpha()):
        return 0
    return 1 if n <= 7 else 2


def distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein distance (adjacent swaps count 1), or limit + 1 as
    soon as it is certainly larger than `limit`."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] \
                    and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _deleted(word: str, k: int) -> Set[str]:
    """`word` and every word made from it by deleting up to k letters."""
    out, edge = {word}, {word}
    for _ in range(k):
        edge = {w[:i] + w[i + 1:] for w in edge for i in range(len(w))}
        out |= edge
    return out


def close(a: str, b: str) -> bool:
    """True if `a` is a small typo of `b` (and not the same word)."""
    k = bound(a, b)
    return a != b and k > 0 and distance(a, b, k) <= k


# -- what the asker may see -------------------------------------------------------------
class Name:
    """A name one asker may see. `shown` (as written: capitals, accents) is
    worked out only when needed - from the text it was found in."""
    __slots__ = ("key", "kind", "canon", "_shown", "_text", "_subject")

    def __init__(self, key: str, shown: Optional[str], kind: str, canon: Tuple[str, ...],
                 text: str = "", subject: str = ""):
        self.key, self.kind, self.canon = key, kind, canon     # canon: canonical place key(s)
        self._shown, self._text, self._subject = shown, text, subject

    @property
    def shown(self) -> str:
        if self._shown is None:
            from .facts import display
            s = display(self._text, self.key)
            if s == self.key and self._subject:
                s = display(self._subject, self.key)
            self._shown = s
        return self._shown


class Names:
    """The names ONE asker may see: places and people in the facts of their
    visible claims (rejected claims left out) and their approved aliases.
    Built by Brain._known_names(viewer) through viewer-checked reads only.
    Nothing else is ever suggested."""

    def __init__(self, names: Iterable[Name] = ()):
        self.by_key: Dict[str, List[Name]] = {}
        for n in names:
            if n.key and not any(o.kind == n.kind for o in self.by_key.get(n.key, [])):
                self.by_key.setdefault(n.key, []).append(n)
        self.first: Dict[str, List[str]] = {}
        for k in self.by_key:
            self.first.setdefault(k.split()[0], []).append(k)
        for ks in self.first.values():
            ks.sort(key=lambda k: -len(k.split()))
        self.words: Set[str] = {w for k in self.by_key for w in k.split()}
        self._deletes: Optional[Dict[str, Set[str]]] = None

    def close_firsts(self, word: str) -> List[str]:
        """First words of names that `word` is a small typo of. Looked up
        through the words left after deleting up to two letters (every word
        within the typo bound shares one), then checked exactly."""
        if self._deletes is None:
            self._deletes = {}
            for f in self.first:
                if len(f) >= 4 and f.isalpha():
                    for d in _deleted(f, 2 if len(f) >= 8 else 1):
                        self._deletes.setdefault(d, set()).add(f)
        if len(word) < 4 or not word.isalpha():
            return []
        cands: Set[str] = set()
        for d in _deleted(word, 2 if len(word) >= 7 else 1):
            cands |= self._deletes.get(d, set())
        return sorted(f for f in cands if close(word, f))

    def __contains__(self, key: str) -> bool:
        return key in self.by_key

    def place_keys(self) -> Set[str]:
        return {k for k, ns in self.by_key.items() if any(n.kind == "place" for n in ns)}

    def shown(self, key: str) -> str:
        ns = self.by_key.get(key)
        return ns[0].shown if ns else key

    def canon(self, key: str) -> Tuple[str, ...]:
        for n in self.by_key.get(key, []):
            if n.kind == "place":
                return n.canon
        return (key,)


# -- relations as words -------------------------------------------------------------------
class RelationVocab:
    """Clue words of every relation: B3's (understand_words.CLUES) plus, for a
    relation added as data, the content words of its question forms and
    labels (no code)."""

    def __init__(self, added: Iterable[dict] = ()):
        self.strong: Dict[str, Set[str]] = {}
        self.weak: Dict[str, Set[str]] = {}
        self.compatible: Dict[str, Set[str]] = {k: set(v) for k, v in W.COMPATIBLE.items()}
        self.templates: Dict[str, List[str]] = {}
        self.labels: Dict[str, dict] = {}
        for rel, (strong, weak) in W.CLUES.items():
            for w in strong:
                self.strong.setdefault(w, set()).add(rel)
            for w in weak:
                self.weak.setdefault(w, set()).add(rel)
        for r in added:
            rel = r["key"]
            self.templates[rel] = list(r.get("question_forms") or [])
            self.labels[rel] = dict(r.get("labels") or {})
            qkinds = {"what"}
            texts = self.templates[rel] + list(self.labels[rel].values())
            for t in texts:
                for w in tokens(t.replace("{place}", " ")):
                    if w in W.QWORDS:
                        qkinds.add(W.QWORDS[w])
                    elif w not in W.FILLER and w not in W.POLITE and w not in W.ROOM \
                            and not w.isdigit() and len(w) > 2:
                        self.strong.setdefault(w, set()).add(rel)
            self.compatible[rel] = qkinds
        self._stems: Dict[str, Set[str]] = {}
        for w, rels in self.strong.items():
            self._stems.setdefault(stem(w), set()).update(rels)
        self.vocab: Dict[str, Tuple[str, frozenset]] = {}   # typo targets: word -> meaning
        for w, rels in self.strong.items():
            self.vocab[w] = ("strong", frozenset(rels))
        for w, rels in self.weak.items():
            self.vocab.setdefault(w, ("weak", frozenset(rels)))
        for w, k in W.QWORDS.items():
            self.vocab.setdefault(w, ("qword", frozenset([k])))
        # ignorable words only as typo targets when long ("plese" -> please; never
        # "time" -> "dime"): a short unknown word must not quietly disappear
        for w in W.POLITE | W.FILLER:
            if len(w) >= 6:
                self.vocab.setdefault(w, ("filler", frozenset()))
        for w in W.ROOM:
            self.vocab.setdefault(w, ("room", frozenset()))
        for w in _DIRS:
            self.vocab.setdefault(w, ("dir", frozenset([_DIRS[w]])))
        # first words of two-word clues ("loks after" -> look after)
        self.phrase_first = {a for a, _ in W.PHRASES if len(a) >= 4}

    def clue(self, word: str) -> Tuple[str, Set[str]]:
        """("strong" | "weak" | "", relations) for one word."""
        if word in self.strong:
            return "strong", set(self.strong[word])
        if word in self.weak:
            return "weak", set(self.weak[word])
        s = stem(word)
        if len(s) > 3 and s in self._stems:
            return "strong", set(self._stems[s])
        return "", set()


# -- the result -------------------------------------------------------------------------------
@dataclass
class Understood:
    kind: str                         # plan | did_you_mean | ambiguous_name | ambiguous_relation
    relation: str = ""
    relations: List[str] = field(default_factory=list)       # ambiguous_relation
    entity: str = ""                  # name key
    shown: str = ""
    inverse: bool = False
    qualifier: str = ""
    candidates: List[Tuple[str, str]] = field(default_factory=list)   # (key, shown)
    corrections: List[Tuple[str, str]] = field(default_factory=list)  # (as written, read as)
    ignored: List[str] = field(default_factory=list)                  # politeness words

    def ask(self) -> Ask:
        if self.inverse:
            return Ask(self.relation, inverse=True, value=self.entity)
        return Ask(self.relation, self.entity, self.qualifier)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "relation": self.relation, "relations": list(self.relations),
                "entity": self.shown, "reverse": self.inverse, "qualifier": self.qualifier,
                "candidates": [s for _, s in self.candidates],
                "corrections": [{"from": a, "to": b} for a, b in self.corrections],
                "ignored": list(self.ignored)}


# -- understanding --------------------------------------------------------------------------
_POLITE_PREFIXES = (("do", "you", "know"), ("does", "anyone", "know"), ("can", "you", "tell"),
                    ("could", "you", "tell"), ("would", "you", "tell"), ("can", "you"),
                    ("could", "you"), ("would", "you"))


def _negated(words: List[str]) -> bool:
    for i, w in enumerate(words):
        if w in NEGATIONS or w in ("cant", "dont", "doesnt", "isnt", "nope"):
            return True
        if w == "t" and i > 0 and words[i - 1] in _CONTRACTION_STEMS:
            return True
    return False


def _exact_names(words: List[str], names: Names) -> List[Tuple[int, int, str]]:
    """(start, end, key) of names found exactly, longest first, left to right."""
    out, i = [], 0
    while i < len(words):
        hit = None
        for k in names.first.get(words[i], ()):
            n = len(k.split())
            if words[i:i + n] == k.split():
                hit = (i, i + n, k)
                break
        if hit:
            out.append(hit)
            i = hit[1]
        else:
            i += 1
    return out


def understand(question: str, names: Names, vocab: RelationVocab) -> Optional[Understood]:
    """The meaning of `question` for one asker, or None (not understood -
    respond() then answers exactly as before)."""
    words = normalize(question).split()
    if not words or len(words) > 30 or _negated(words):
        return None
    # the past or the future: refused - except a present passive ("which room
    # is maintained by Mira?")
    timed = [w for w in words if w in W.TIME]
    if timed and not (set(timed) <= set(W.PARTICIPLES) and {"is", "are", "es"} & set(words)):
        return None
    n = len(words)
    role = [""] * n                 # name | clue | qword | dir | filler | polite | room
    rels: List[Set[str]] = [set() for _ in range(n)]
    strength = [""] * n
    corrections: List[Tuple[str, str]] = []

    # 2. exact names
    found = _exact_names(words, names)
    for a, b, _ in found:
        for i in range(a, b):
            role[i] = "name"

    # 3. explain the other words (two-word phrases first)
    i = 0
    while i < n - 1:
        ph = W.PHRASES.get((words[i], words[i + 1]))
        if not ph and not role[i] and not role[i + 1] and words[i] not in vocab.vocab:
            # a typo in the first word of a two-word clue ("loks after")
            near = {a for a in vocab.phrase_first if close(words[i], a)
                    and (a, words[i + 1]) in W.PHRASES}
            meanings = {W.PHRASES[(a, words[i + 1])] for a in near}
            if len(meanings) == 1:
                a = sorted(near)[0]
                corrections.append((words[i], a))
                words[i], ph = a, meanings.pop()
        if ph and not role[i] and not role[i + 1]:
            for j in (i, i + 1):
                role[j], rels[j], strength[j] = "clue", set(ph), "strong"
            i += 2
            continue
        i += 1
    for i, w in enumerate(words):
        if role[i]:
            continue
        s, r = vocab.clue(w)
        if s:
            role[i], rels[i], strength[i] = "clue", r, s
        elif w in W.QWORDS:
            role[i] = "qword"
        elif w in _DIRS:
            role[i] = "dir"
        elif w in W.ROOM:
            role[i] = "room"
        elif w in W.FILLER:
            role[i] = "filler"
        elif w in W.POLITE:
            role[i] = "polite"

    # 5. a name with typos is only suggested; a second (misspelt) name is refused
    windows = _close_names(words, role, names)
    suggestion: List[Tuple[str, str]] = []
    if windows and found:
        return None
    if windows:
        spans = {(a, b) for a, b, _ in windows}
        if any(not (a2 < b1 and a1 < b2) for a1, b1 in spans for a2, b2 in spans):
            return None                               # two names in different places
        for a, b, _ in windows:
            for i in range(a, b):
                role[i] = "name"
        keys = sorted({k for _, _, k in windows})
        # one name written with a typo is suggested as that name (an alias stays
        # the alias the person was typing); several are one per place
        suggestion = [(keys[0], names.shown(keys[0]))] if len(keys) == 1 else \
            _by_canon(keys, names)
    elif not found:
        span, cands = _partial(words, role, names)
        if span is not None:
            if len(cands) < 2:
                return None                           # a partial name is never the whole name
            for i in range(*span):
                role[i] = "name"
            suggestion = cands

    # 4. typos in the remaining words: one meaning, or nothing
    for i, w in enumerate(words):
        if role[i]:
            continue
        hits = sorted((distance(w, v, bound(w, v)), v, m) for v, m in vocab.vocab.items()
                      if close(w, v))
        if not hits:
            return None                               # an unexplained word: refused
        meanings = {m for _, _, m in hits}
        kinds = {m[0] for m in meanings}
        if len(kinds) != 1:
            return None
        kind = kinds.pop()
        if kind in ("strong", "weak"):
            role[i], rels[i], strength[i] = "clue", set().union(*(m[1] for m in meanings)), kind
        elif kind in ("qword", "dir"):
            if len(meanings) != 1:
                return None
            role[i] = kind
        elif kind == "room":
            role[i] = "room"
        else:
            role[i] = "filler"
        corrections.append((w, hits[0][1] if len(meanings) == 1 else
                            "/".join(sorted({v for _, v, _ in hits}))))
        words[i] = hits[0][1]

    # 6. refusals, then the meaning
    qkinds = {W.QWORDS[w] for w, r in zip(words, role) if r == "qword" and w in W.QWORDS}
    dirs = {_DIRS[w] for w, r in zip(words, role) if r == "dir"}
    if len(dirs) > 1 or _yes_no(words, qkinds):
        return None
    strong = [rels[i] for i in range(n) if role[i] == "clue" and strength[i] == "strong"]
    weak = [rels[i] for i in range(n) if role[i] == "clue" and strength[i] == "weak"]
    clue_sets = strong or weak
    if not clue_sets:
        return None
    common = set.intersection(*clue_sets)             # every clue word must fit the relation
    if not common or (len(common) > 1 and "leads_to" in common):
        return None                                   # two different asks (e.g. a chain)
    if dirs and common != {"leads_to"}:
        return None
    if common == {"leads_to"} and "name" in role and \
            _before(words, role, role.index("name")) in W.INTO:
        return None                                   # "what leads TO the X": other direction
    ignored = [w for w, r in zip(words, role) if r == "polite"]

    entities = sorted({k for _, _, k in found})
    if len(entities) > 1:
        return None
    keys = entities or [s for s, _ in suggestion]
    if not keys:
        return None
    kinds = {x.kind for k in keys for x in names.by_key[k]}
    if len(kinds) != 1:
        return None                                   # a person and a place share the name
    inverse = kinds.pop() == "person"
    if inverse:
        common &= set(W.INVERSE_RELATIONS)
        if not common or (qkinds - W.COMPATIBLE_INVERSE) or \
                not (qkinds or "room" in role):
            return None
    else:
        common = {r for r in common if not (qkinds - vocab.compatible.get(r, {"what"}))}
        if not common:
            return None                               # "when is the X maintained?"
    base = dict(relations=sorted(common), inverse=inverse, qualifier=dirs.pop() if dirs else "",
                corrections=corrections, ignored=ignored)
    relation = sorted(common)[0] if len(common) == 1 else ""
    if entities:
        k = entities[0]
        kind = "plan" if relation else "ambiguous_relation"
        return Understood(kind, relation, entity=k, shown=names.shown(k), **base)
    if not relation:
        return None                                   # unclear name AND unclear relation
    if len(suggestion) == 1:
        k, shown = suggestion[0]
        return Understood("did_you_mean", relation, entity=k, shown=shown, candidates=suggestion,
                          **base)
    return Understood("ambiguous_name", relation, candidates=sorted(suggestion, key=lambda x: x[1]),
                      **base)


def _before(words: List[str], role: List[str], i: int) -> str:
    """The word before position i, articles skipped."""
    j = i - 1
    while j >= 0 and words[j] in ("the", "el", "la", "los", "las"):
        j -= 1
    return words[j] if j >= 0 else ""


def _yes_no(words: List[str], qkinds: Set[str]) -> bool:
    """A yes / no question (left for a later milestone): it starts with an
    auxiliary ("Does ...", "Is ...", "¿Tiene ...") and has no question word."""
    if qkinds:
        return False
    rest = list(words)
    for p in _POLITE_PREFIXES:
        if tuple(rest[:len(p)]) == p:
            rest = rest[len(p):]
            break
    return bool(rest) and rest[0] in W.YESNO


def _close_names(words: List[str], role: List[str], names: Names) -> List[Tuple[int, int, str]]:
    """Names written with small typos: every word of the name matches the
    question's word in that position exactly or within the typo bound, at
    least one differs, and every differing word is one the question does not
    otherwise explain."""
    out = []
    n = len(words)
    for i in range(n):
        if role[i] == "name":
            continue
        cands = ([words[i]] if words[i] in names.first else []) + \
            (names.close_firsts(words[i]) if not role[i] else [])
        for f in cands:
            for k in names.first[f]:
                kw = k.split()
                j = i + len(kw)
                if j > n or any(role[x] == "name" for x in range(i, j)):
                    continue
                diff = 0
                for x, want in zip(range(i, j), kw):
                    if words[x] == want:
                        continue
                    if role[x] or not close(words[x], want):
                        break
                    diff += distance(words[x], want, bound(words[x], want))
                else:
                    if 0 < diff <= MAX_NAME_EDITS:
                        out.append((i, j, k, diff))
    # as with exact names, the longer name wins over one inside it ("Faro Nort":
    # the Faro Norte, not the person Nora)
    out = [w for w in out if not any(o[0] <= w[0] and w[1] <= o[1] and (o[1] - o[0]) >
                                     (w[1] - w[0]) for o in out)]
    # only the closest names; names equally close all stay (ambiguous)
    best = min((w[3] for w in out), default=0)
    return [(a, b, k) for a, b, k, d in out if d == best]


def _by_canon(keys: List[str], names: Names) -> List[Tuple[str, str]]:
    """One (key, shown) per place: an alias of a place counts as that place,
    and the place's own name is preferred ("Faro del Norte" -> Faro Norte)."""
    by: Dict[Tuple[str, ...], str] = {}
    for k in sorted(keys):
        c = names.canon(k)
        if c not in by or (len(c) == 1 and k == c[0]):
            by[c] = c[0] if len(c) == 1 and c[0] in names else k
    return sorted(((k, names.shown(k)) for k in by.values()), key=lambda x: x[1])


def _partial(words: List[str], role: List[str], names: Names):
    """A part of a name the question does not otherwise explain ("the Faro"):
    (its span, the places whose name contains it). Two or more places =
    ambiguous; one is NOT resolved (a partial name is never the whole name).
    (None, []) if no unexplained run of words is part of a name."""
    runs, start = [], None
    for i, r in enumerate(role + ["end"]):
        if not r and start is None:
            start = i
        elif r and start is not None:
            runs.append((start, i))
            start = None
    hits = []
    for a, b in runs:
        padded = " " + " ".join(words[a:b]) + " "
        inside = [k for k in names.place_keys() if padded in f" {k} " and k != padded.strip()]
        if inside:
            hits.append(((a, b), _by_canon(inside, names)))
    if len(hits) != 1:
        return (None, []) if not hits else ((0, 0), [])   # several partial names: refused
    return hits[0]


# -- wording ------------------------------------------------------------------------------------
_ES_DIR = {"north": "norte", "south": "sur", "east": "este", "west": "oeste"}

# a question SK understands EXACTLY (B3's grammar or the rules above with an
# exact name) - used for the choices a person can tap
_CANON = {
    "en": {"caretaker": "Who maintains the {p}?", "runs": "Who runs the {p}?",
           "room_id": "What is the room ID of the {p}?",
           "quest_reward": "How many coins does the quest in the {p} give?",
           "leads_to": "Where does the exit of the {p} lead?",
           "leads_to_q": "Where does the {q} exit of the {p} lead?",
           "inv_caretaker": "Which room does {p} look after?",
           "inv_runs": "Which room does {p} run?"},
    "es": {"caretaker": "¿Quién es el encargado de {p}?", "runs": "¿Quién dirige {p}?",
           "room_id": "¿Cuál es el ID de sala de {p}?",
           "quest_reward": "¿Cuántas monedas da la misión de {p}?",
           "leads_to": "¿A dónde lleva la salida de {p}?",
           "leads_to_q": "¿A dónde lleva la salida {q} de {p}?",
           "inv_caretaker": "¿Qué sala cuida {p}?", "inv_runs": "¿Qué sala dirige {p}?"},
}
_ABOUT = {
    "en": {"caretaker": "who looks after the {p}", "runs": "who runs the {p}",
           "room_id": "the room ID of the {p}", "quest_reward": "the quest reward in the {p}",
           "leads_to": "where the exits of the {p} lead",
           "leads_to_q": "where the {q} exit of the {p} leads",
           "inv_caretaker": "which room {p} looks after", "inv_runs": "which room {p} runs",
           "added": "{label}: the {p}"},
    "es": {"caretaker": "quién cuida {p}", "runs": "quién dirige {p}",
           "room_id": "el ID de sala de {p}", "quest_reward": "la recompensa de la misión de {p}",
           "leads_to": "a dónde llevan las salidas de {p}",
           "leads_to_q": "a dónde lleva la salida {q} de {p}",
           "inv_caretaker": "qué sala cuida {p}", "inv_runs": "qué sala dirige {p}",
           "added": "{label}: {p}"},
}


def _form(relation: str, qualifier: str, inverse: bool) -> str:
    if inverse:
        return "inv_" + relation
    if relation == "leads_to" and qualifier:
        return "leads_to_q"
    return relation


def question_for(relation: str, shown: str, lang: str, vocab: RelationVocab,
                 qualifier: str = "", inverse: bool = False) -> str:
    """A question about `shown` that SK understands exactly (for a choice)."""
    lang = lang if lang in _CANON else "en"
    form = _form(relation, qualifier, inverse)
    t = _CANON[lang].get(form)
    if t:
        q = _ES_DIR.get(qualifier, qualifier) if lang == "es" else qualifier
        return t.format(p=shown, q=q)
    forms = vocab.templates.get(relation) or []
    pick = [f for f in forms if language(f) == lang] or forms
    if not pick:
        return ""
    q = pick[0].replace("{place}", shown).strip()
    q = q[:1].upper() + q[1:] + "?"
    return ("¿" + q) if lang == "es" else q


def about(relation: str, shown: str, lang: str, vocab: RelationVocab, qualifier: str = "",
          inverse: bool = False) -> str:
    """What was understood, in words ("who looks after the Lantern Garden")."""
    lang = lang if lang in _ABOUT else "en"
    form = _form(relation, qualifier, inverse)
    t = _ABOUT[lang].get(form)
    q = _ES_DIR.get(qualifier, qualifier) if lang == "es" else qualifier
    if t:
        return t.format(p=shown, q=q)
    labels = vocab.labels.get(relation) or {}
    label = labels.get(lang) or labels.get("en") or relation
    return _ABOUT[lang]["added"].format(label=label, p=shown)


# -- language ---------------------------------------------------------------------------------------
def language(question: str, names: Optional[Names] = None) -> str:
    """"es" or "en" by the words of the question: an opening "¿" / "¡", "ñ"
    and Spanish words against English words. Words inside a name the asker
    may see are not counted ("Plaza del Puerto" in an English question). A
    tie is English, unless the question opens with "¿" / "¡"."""
    q = question or ""
    words = normalize(q).split()
    if names is not None and words:
        keep = [True] * len(words)
        for a, b, _ in _exact_names(words, names):
            for i in range(a, b):
                keep[i] = False
        words = [w for w, k in zip(words, keep) if k]
    es = sum(0.5 if w in W.ES_WEAK else 1 for w in words if w in W.ES) + \
        (2 if ("¿" in q or "¡" in q) else 0) + (1 if "ñ" in q.lower() else 0)
    en = sum(1 for w in words if w in W.EN)
    if es != en:
        return "es" if es > en else "en"
    return "es" if ("¿" in q or "¡" in q) else "en"
