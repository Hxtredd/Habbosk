"""Built-in solvers: instant, offline, no AI.

They only answer when the result is certain AND exactly one option on screen
matches it. Anything else returns None (the question then waits for memory
or AI). A wrong click costs the player a point, so refusing is always better
than guessing.

  * ORDENAR with sums      '9 + 12', '63 − 8' ...  -> computed and ordered
  * sequences              '¿Qué letra sigue? F, J, N, R, ?'  -> V
                           '2, 4, 8, ?'  -> 16   (also missing / previous term)
  * arithmetic questions   '¿Cuánto es 7 × 8?'   -> 56
  * wrong equation         ENCUENTRA EL ERROR with '10 − 4 = 5' among options
  * simple word problems   '45 minutos y luego 25 minutos más'  -> 70
                           (reported as LIKELY, never as certain)
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

from .model import CHOICE, ORDER, Answer, Question
from .text import normalize, strip_accents

# ---------------------------------------------------------------------------
# A small, safe arithmetic parser (no eval)
# ---------------------------------------------------------------------------
_OPS = {"−": "-", "–": "-", "—": "-", "×": "*", "x": "*", "X": "*", "·": "*",
        "÷": "/", ":": "/"}


def _tokens(expr: str) -> Optional[List[str]]:
    s = "".join(_OPS.get(c, c) for c in expr.strip())
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)                 # 1,5 -> 1.5
    toks = re.findall(r"\d+(?:\.\d+)?|[+\-*/()]", s)
    if "".join(toks) != re.sub(r"\s+", "", s):
        return None                                       # something that isn't arithmetic
    return toks


def evaluate(expr: str) -> Optional[Fraction]:
    """'63 − 8' -> 55, '(2 + 3) × 4' -> 20, '7' -> 7. None if not arithmetic."""
    toks = _tokens(expr)
    if not toks:
        return None
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def atom():
        t = peek()
        if t is None:
            raise ValueError
        if t == "(":
            take()
            v = expr_()
            if take() != ")":
                raise ValueError
            return v
        if t == "-":
            take()
            return -atom()
        take()
        return Fraction(t)

    def term():
        v = atom()
        while peek() in ("*", "/"):
            op, rhs = take(), atom()
            if op == "/":
                if rhs == 0:
                    raise ValueError
                v = v / rhs
            else:
                v = v * rhs
        return v

    def expr_():
        v = term()
        while peek() in ("+", "-"):
            op, rhs = take(), term()
            v = v + rhs if op == "+" else v - rhs
        return v

    try:
        v = expr_()
        return v if pos == len(toks) else None
    except (ValueError, ZeroDivisionError, IndexError):
        return None


def fmt(v: Fraction) -> str:
    return str(v.numerator) if v.denominator == 1 else f"{float(v):g}".replace(".", ",")


def _num_option(o: str) -> Optional[Fraction]:
    o = o.strip().rstrip(".")
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", o):
        return Fraction(o.replace(",", "."))
    return None


def _pick(options: Sequence[str], value) -> Optional[str]:
    """The ONE option equal to value (number or letter); None if 0 or 2+."""
    hits = []
    for o in options:
        if isinstance(value, str):
            if normalize(o) == normalize(value):
                hits.append(o)
        else:
            n = _num_option(o)
            if n is not None and n == value:
                hits.append(o)
    return hits[0] if len(hits) == 1 else None


def _es(q: Question) -> bool:
    return q.language != "en"


# ---------------------------------------------------------------------------
# ORDENAR with sums
# ---------------------------------------------------------------------------
_ASC = ("menor a mayor", "mas pequeno", "mas bajo", "ascendente", "smallest to largest",
        "lowest to highest", "least to greatest", "ascending")
_DESC = ("mayor a menor", "mas grande", "mas alto", "descendente", "largest to smallest",
         "highest to lowest", "greatest to least", "descending")


def _arith_core(o: str) -> str:
    """'> 36 + 10' / '36 +10 Jj' -> '36 + 10': drop a stray symbol in front and
    stray letters at the end (icons, pointer). Digits are never dropped."""
    o = re.sub(r"^[^\d(\-−]+", "", o.strip())
    return re.sub(r"(?:\s+[A-Za-z]{1,3})+$", "", o).strip()


_HAS_OP = re.compile(r"\d\s*[-+−–x×X*÷:/]\s*\(?\d")


def solve_order(q: Question) -> Optional[Answer]:
    if q.kind != ORDER:
        return None
    t = strip_accents(q.text).lower()
    asc = any(k in t for k in _ASC)
    desc = any(k in t for k in _DESC)
    if asc == desc:
        return None                                       # direction unknown
    if ("cuenta" in t or "operacion" in t or "resultado" in t) and \
            not all(_HAS_OP.search(_arith_core(o)) for o in q.options):
        return None             # every option must be a sum: '2055' means a misread sign
    vals = [evaluate(o) if evaluate(o) is not None else evaluate(_arith_core(o))
            for o in q.options]
    if None in vals or len(set(vals)) != len(vals):
        return None                                       # not all sums, or ties
    pairs = sorted(zip(vals, q.options), reverse=desc)
    order = [o for _, o in pairs]
    shown = ", ".join(f"{o} = {fmt(v)}" for v, o in pairs)
    expl = (f"Resultados: {shown}." if _es(q) else f"Results: {shown}.")
    return Answer(ORDER, order=order, explanation=expl, source="solver", confidence=1.0)


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------
_SEQ_ITEM = r"(?:-?\d+(?:[.,]\d+)?|[A-Za-zÑñ]|\?|_+)"
_SEQ_RE = re.compile(rf"{_SEQ_ITEM}(?:\s*[,;]\s*{_SEQ_ITEM}){{2,}}")


def _find_sequence(q: Question) -> Optional[List[str]]:
    for src in list(q.clues) + [q.text]:
        for m in _SEQ_RE.finditer(src):
            items = [x.strip() for x in re.split(r"[,;]", m.group(0))]
            if len(items) >= 3:
                return items
    return None


def _rules(known: List[Tuple[int, Fraction]], n: int):
    """Yield (rule description, function index -> value) for every simple rule
    that fits ALL known terms. Indices 0..n-1; one of them is unknown."""
    idx = [i for i, _ in known]
    consecutive = idx == list(range(idx[0], idx[0] + len(idx)))

    # constant step (works with a gap: a + i*d)
    if len(known) >= 3:
        (i0, a0), (i1, a1) = known[0], known[1]
        d = (a1 - a0) / (i1 - i0)
        if all(a0 + (i - i0) * d == x for i, x in known):
            yield ("step", d), (lambda i, a0=a0, i0=i0, d=d: a0 + (i - i0) * d)
    # constant ratio
    if len(known) >= 3 and all(x != 0 for _, x in known) and consecutive:
        r = known[1][1] / known[0][1]
        if r not in (0, 1) and all(known[k + 1][1] == known[k][1] * r for k in range(len(known) - 1)):
            a0, i0 = known[0][1], known[0][0]
            yield ("ratio", r), (lambda i, a0=a0, i0=i0, r=r: a0 * r ** (i - i0))
    if not consecutive:
        return
    seq = [x for _, x in known]
    first = idx[0]
    # differences growing by a constant (e.g. +2, +3, +4 ... or squares)
    if len(seq) >= 4:
        d1 = [b - a for a, b in zip(seq, seq[1:])]
        d2 = [b - a for a, b in zip(d1, d1[1:])]
        if len(set(d2)) == 1 and d2[0] != 0:
            def f(i, seq=seq, d1=d1, c=d2[0], first=first):
                k = i - first
                if 0 <= k < len(seq):
                    return seq[k]
                if k == len(seq):
                    return seq[-1] + d1[-1] + c
                if k == -1:
                    return seq[0] - (d1[0] - c)
                return None
            yield ("growing", d2[0]), f
    # With only 3 terms, steps growing by a constant are an equally good reading
    # ('1, 2, 4' -> 7 (+1 +2 +3) or 8 (x2)). Offer it so that, when both answers
    # are options, the solver sees the ambiguity and refuses.
    if len(seq) == 3:
        d1 = [seq[1] - seq[0], seq[2] - seq[1]]
        c = d1[1] - d1[0]
        if c != 0:
            def f3(i, seq=seq, d1=d1, c=c, first=first):
                k = i - first
                if 0 <= k < 3:
                    return seq[k]
                if k == 3:
                    return seq[2] + d1[1] + c
                return None
            yield ("growing", c), f3
    # two alternating steps (+3, -1, +3, -1 ...)
    if len(seq) >= 5:
        d1 = [b - a for a, b in zip(seq, seq[1:])]
        if len(set(d1[0::2])) == 1 and len(set(d1[1::2])) == 1 and d1[0] != d1[1]:
            def g(i, seq=seq, d1=d1, first=first):
                k = i - first
                if 0 <= k < len(seq):
                    return seq[k]
                if k == len(seq):
                    return seq[-1] + d1[(k - 1) % 2]
                return None
            yield ("alternate", (d1[0], d1[1])), g
    # each term = sum of the two before
    if len(seq) >= 4 and all(seq[k] == seq[k - 1] + seq[k - 2] for k in range(2, len(seq))):
        def h(i, seq=seq, first=first):
            k = i - first
            if 0 <= k < len(seq):
                return seq[k]
            if k == len(seq):
                return seq[-1] + seq[-2]
            return None
        yield ("fibonacci", None), h


def solve_sequence(q: Question) -> Optional[Answer]:
    if q.kind != CHOICE:
        return None
    items = _find_sequence(q)
    if not items:
        return None
    t = strip_accents(q.text).lower()
    unknown = [i for i, x in enumerate(items) if x == "?" or x.startswith("_")]
    if not unknown:
        if any(w in t for w in ("antes", "anterior", "precede", "before")):
            items = ["?"] + items
        elif any(w in t for w in ("sigue", "siguiente", "despues", "next", "comes")):
            items = items + ["?"]
        else:
            return None
        unknown = [items.index("?")]
    if len(unknown) != 1:
        return None
    pos = unknown[0]
    letters = all(re.fullmatch(r"[A-Za-z]", x) for i, x in enumerate(items) if i != pos)
    numbers = all(re.fullmatch(r"-?\d+(?:[.,]\d+)?", x) for i, x in enumerate(items) if i != pos)
    if not (letters or numbers):
        return None

    def val(x):
        return Fraction(ord(x.upper()) - 64) if letters else Fraction(x.replace(",", "."))
    known = [(i, val(x)) for i, x in enumerate(items) if i != pos]
    preds = {}
    for rule, f in _rules(known, len(items)):
        p = f(pos)
        if p is None:
            continue
        if letters:
            if p.denominator != 1 or not 1 <= p <= 26:
                continue
            p = chr(64 + int(p))
        preds.setdefault(p, rule)
    hits = {p: r for p, r in preds.items() if _pick(q.options, p) is not None}
    if len(hits) != 1:
        return None                                     # no rule fits, or rules disagree
    p, rule = next(iter(hits.items()))
    choice = _pick(q.options, p)
    return Answer(CHOICE, choice=choice, explanation=_explain_rule(rule, letters, p, items, pos, q),
                  source="solver", confidence=1.0)


def _explain_rule(rule, letters: bool, p, items, pos, q: Question) -> str:
    es = _es(q)
    kind, k = rule
    unit = ("letra" if letters else "número") if es else ("letter" if letters else "number")
    if kind == "step":
        n = fmt(k) if not isinstance(k, str) else k
        sign = "+" if k > 0 else ""
        if es:
            where = " en el alfabeto" if letters else ""
            return f"Cada {unit} salta {sign}{n}{where}: la respuesta es {p}."
        return f"Each {unit} moves {sign}{n}: the answer is {p}."
    if kind == "ratio":
        return (f"Cada número se multiplica por {fmt(k)}: la respuesta es {p}." if es
                else f"Each number is multiplied by {fmt(k)}: the answer is {p}.")
    if kind == "growing":
        return (f"Los saltos crecen de {fmt(k)} en {fmt(k)}: la respuesta es {p}." if es
                else f"The steps grow by {fmt(k)} each time: the answer is {p}.")
    if kind == "alternate":
        a, b = (fmt(x) for x in k)
        return (f"Los saltos se alternan ({a}, {b}): la respuesta es {p}." if es
                else f"The steps alternate ({a}, {b}): the answer is {p}.")
    return (f"Cada número es la suma de los dos anteriores: la respuesta es {p}." if es
            else f"Each number is the sum of the two before: the answer is {p}.")


# ---------------------------------------------------------------------------
# Arithmetic questions and wrong equations
# ---------------------------------------------------------------------------
_CALC_RE = re.compile(r"(?:cu[aá]nto es|cu[aá]l es el resultado de|resultado de|calcula|"
                      r"how much is|what is)\s*:?\s*([\d\s+\-−–×x*÷/:().,]+?)\s*\??$", re.I)


def solve_calculation(q: Question) -> Optional[Answer]:
    if q.kind != CHOICE:
        return None
    m = _CALC_RE.search(q.text.strip())
    if not m:
        return None
    expr = m.group(1).strip().rstrip("=").strip()
    if not re.search(r"\d\s*[+\-−–×x*÷/:]\s*\d", expr):
        return None
    v = evaluate(expr)
    if v is None:
        return None
    choice = _pick(q.options, v)
    if choice is None:
        return None
    return Answer(CHOICE, choice=choice, explanation=f"{expr} = {fmt(v)}", source="solver",
                  confidence=1.0)


def solve_wrong_equation(q: Question) -> Optional[Answer]:
    """ENCUENTRA EL ERROR / ¿cuál es falsa? when every option is 'a op b = c'."""
    if q.kind != CHOICE:
        return None
    t = normalize(q.category + " " + q.text)
    wants_wrong = any(w in t.split() for w in ("error", "falsa", "falso", "incorrecta",
                                                "incorrecto", "false", "wrong", "incorrect"))
    wants_right = any(w in t.split() for w in ("correcta", "correcto", "verdadera",
                                                "verdadero", "true", "correct"))
    if wants_wrong == wants_right:
        return None
    truth = []
    for o in q.options:
        if o.count("=") != 1:
            return None
        lhs, rhs = o.split("=")
        a, b = evaluate(lhs), evaluate(rhs)
        if a is None or b is None:
            return None
        truth.append(a == b)
    pick = [o for o, ok in zip(q.options, truth) if ok != wants_wrong]
    if len(pick) != 1:
        return None
    lhs, rhs = (x.strip() for x in pick[0].split("="))
    real = fmt(evaluate(lhs))
    if not wants_wrong:
        expl = f"{lhs} = {real}"
    else:
        expl = f"{lhs} = {real}, " + ("no " if _es(q) else "not ") + rhs
    return Answer(CHOICE, choice=pick[0], explanation=expl, source="solver", confidence=1.0)


# ---------------------------------------------------------------------------
# Simple word problems  (reported as LIKELY, never certain)
# ---------------------------------------------------------------------------
_ADD = ("mas", "luego", "suma", "sumas", "total", "juntos", "en total", "anade", "anades",
        "agregas", "ganas", "recibes", "more", "plus", "total", "together", "add")
_SUB = ("menos", "quedan", "queda", "gastas", "pierdes", "regalas", "das", "resta", "restan",
        "left", "minus", "spend", "lose", "give away")
_MUL = ("cada", "veces", "por cada", "each", "times")


def solve_word_problem(q: Question) -> Optional[Answer]:
    if q.kind != CHOICE:
        return None
    t = strip_accents(q.text).lower()
    if not re.search(r"cuant[oa]s?|how many|how much", t):
        return None
    nums = [Fraction(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", q.text)]
    if not 2 <= len(nums) <= 3 or not all(_num_option(o) is not None for o in q.options):
        return None
    words = set(re.findall(r"[a-z]+", t))
    ops = set()
    if words & set(_ADD) or "en total" in t:
        ops.add("+")
    if words & set(_SUB):
        ops.add("-")
    if words & set(_MUL):
        ops.add("*")
    if "doble" in words or "double" in words or "triple" in words or "mitad" in words or "half" in words:
        return None                                       # unusual phrasing: leave it to AI
    if len(ops) != 1:
        return None                                       # unclear: don't guess
    op = ops.pop()
    if op == "+":
        v, expr = sum(nums), " + ".join(fmt(n) for n in nums)
    elif op == "-":
        v = nums[0] - sum(nums[1:])
        expr = " − ".join(fmt(n) for n in nums)
    else:
        if len(nums) != 2:
            return None
        v, expr = nums[0] * nums[1], f"{fmt(nums[0])} × {fmt(nums[1])}"
    choice = _pick(q.options, v)
    if choice is None:
        return None
    return Answer(CHOICE, choice=choice, explanation=f"{expr} = {fmt(v)}", source="solver",
                  confidence=0.7)


# ---------------------------------------------------------------------------
def solve_question(q: Question) -> Optional[Answer]:
    """Try every built-in solver. Returns an Answer with source='solver';
    confidence 1.0 = certain, below 1.0 = likely (shown as 'Probably')."""
    for fn in (solve_order, solve_sequence, solve_calculation, solve_wrong_equation,
               solve_word_problem):
        try:
            a = fn(q)
        except Exception:
            a = None
        if a is not None:
            return a
    return None
