"""Conservative arithmetic checks for explicit numeric claims.

Only equations with an unambiguous percentage or multiplication are evaluated.
Other numeric claims continue through evidence-based fact checking.
"""
from __future__ import annotations

import re
from decimal import Decimal

_UNIT = r"(?:billions?|millions?|thousands?|[kmb])\b"  # \b: "6 months" / "100 km" are not 6M / 100k
_AMOUNT = rf"\$?\s*\d+(?:,\d{{3}})*(?:\.\d+)?(?:\s*{_UNIT})?"
_PERCENT = re.compile(rf"(\d+(?:\.\d+)?)\s*%\s+of\s+({_AMOUNT})\s*(?:=|is|equals)\s*({_AMOUNT})", re.I)
_TIMES = re.compile(rf"({_AMOUNT})\s*(?:×|\*|\bx\b)\s*({_AMOUNT})\s*(?:=|is|equals)\s*({_AMOUNT})", re.I)
_NUMBER = re.compile(rf"\$?\s*(\d+(?:,\d{{3}})*(?:\.\d+)?)(?:\s*({_UNIT}))?", re.I)
_SCALE = {"thousand": 1_000, "k": 1_000, "million": 1_000_000, "m": 1_000_000,
          "billion": 1_000_000_000, "b": 1_000_000_000}


def _value(text: str) -> Decimal:
    match = _NUMBER.fullmatch(text.strip())
    if not match:
        raise ValueError(text)
    unit = (match.group(2) or "").lower().rstrip("s")
    return Decimal(match.group(1).replace(",", "")) * _SCALE.get(unit, 1)


_OPERATOR_END = re.compile(r"(?:[+×*/−-]|\bx)\s*$", re.I)


def check_arithmetic(text: str) -> bool | None:
    """Return True/False for a clear equation, or None when there is no safe calculation.

    A match that is only the tail of a longer expression ("28 x $36,000 + [41 x $96,000 = $4,944,000]") is not an
    equation of its own, so it is left to check_calculation, which evaluates the whole left-hand side.
    """
    match = _PERCENT.search(text) or _TIMES.search(text)
    if not match or _OPERATOR_END.search(text[:match.start()]):
        return None
    if match.re is _PERCENT:
        expected = Decimal(match.group(1)) * _value(match.group(2)) / 100
    else:
        expected = _value(match.group(1)) * _value(match.group(2))
    stated = _value(match.group(3))
    return abs(expected - stated) <= max(Decimal("0.005"), abs(expected) * Decimal("0.005"))


# --- Step-by-step calculations shown by agents ("4 cities x $110B x 20% = $88B; $88B / 2 = $44B") ----------
_TOKEN = re.compile(
    rf"(?P<num>\$?\s*\d+(?:,\d{{3}})*(?:\.\d+)?(?:\s*%|(?:\s*{_UNIT}))?)"
    r"|(?P<op>[×*/+]|(?<=\s)[-−](?=\s)|(?<=\s)x(?=\s))"
    r"|(?P<word>[A-Za-z][A-Za-z'’-]*)"
    r"|(?P<paren>[()])",
    re.I,  # "$110B", "$14.2M": unit letters are usually upper case
)
_STEP_TOLERANCE = Decimal("0.015")  # stated results are usually rounded ("~$22B")


def _amount(token: str) -> Decimal:
    token = token.strip()
    if token.endswith("%"):
        return Decimal(token[:-1].strip().lstrip("$").replace(",", "")) / 100
    return _value(token)


def _evaluate(expr: str) -> Decimal | None:
    """Numbers and + - x / only; words between operands ("4 cities x $110B budget") are ignored.

    Returns None whenever the expression is not a clean alternation of numbers and operators, so prose,
    ranges ("$10-20M") or rates ("$5k/site") are never guessed at.
    """
    tokens = []
    for m in _TOKEN.finditer(expr):
        if m.group("num"):
            tokens.append(("num", _amount(m.group("num"))))
        elif m.group("op"):
            tokens.append(("op", {"×": "*", "x": "*", "−": "-"}.get(m.group("op"), m.group("op"))))
        elif m.group("paren"):
            return None  # keep the evaluator tiny: no grouping
    if not tokens or len(tokens) % 2 == 0:
        return None
    if any(kind != ("num" if i % 2 == 0 else "op") for i, (kind, _) in enumerate(tokens)):
        return None
    # Standard precedence: fold * and / first, then + and -.
    terms, ops = [tokens[0][1]], []
    for i in range(1, len(tokens), 2):
        op, value = tokens[i][1], tokens[i + 1][1]
        if op in "*/":
            if op == "/" and value == 0:
                return None
            terms[-1] = terms[-1] * value if op == "*" else terms[-1] / value
        else:
            ops.append(op)
            terms.append(value)
    total = terms[0]
    for op, value in zip(ops, terms[1:]):
        total = total + value if op == "+" else total - value
    return total


_POINTS = re.compile(r"\s*(?:%|pp\b|percentage points?|points?|pts?\b|percent)", re.I)


def _has_operator(text: str) -> bool:
    return any(m.group("op") for m in _TOKEN.finditer(text))


def _first_number(text: str) -> tuple[Decimal, str, str] | None:
    """(signed value, number as written, text after it) of the first number in a result."""
    clean = text.replace("~", "").replace("≈", "")
    for m in _TOKEN.finditer(clean):
        if m.group("num"):
            value = _amount(m.group("num"))
            if re.search(r"(?:^|[\s(])[-−]\s*$", clean[:m.start()]):   # "= -$7,000,000", "= −79"
                value = -value
            return value, m.group("num"), clean[m.end():]
    return None


def _half_unit(written: str, value: Decimal) -> Decimal:
    """Half a unit of the last digit written: "2.3" stands for 2.25-2.35, "$22B" for $21.5B-$22.5B."""
    m = re.search(r"\d+(?:,\d{3})*(?:\.(\d+))?", written)
    mantissa = Decimal(m.group(0).replace(",", "")) if m else Decimal(0)
    if not mantissa:
        return Decimal(0)
    return abs(value) * Decimal("0.5") * Decimal(10) ** -len(m.group(1) or "") / mantissa


def _agrees(expected: Decimal, value: Decimal, written: str, after: str, lhs: str) -> bool:
    tolerance = max(Decimal("0.005"), abs(expected) * _STEP_TOLERANCE, _half_unit(written, value))
    if abs(expected - value) <= tolerance:
        return True
    # Percentage points: "57.4% - 53.9% = 3.5 points", "$100k / $10.2M = 1.0 percent"
    if not written.strip().endswith("%") and ("%" in lhs or _POINTS.match(after)):
        return abs(expected * 100 - value) <= max(Decimal("0.005"), abs(expected * 100) * _STEP_TOLERANCE,
                                                  _half_unit(written, value))
    return False


def check_calculation(text: str) -> bool | None:
    """Check every 'expression = result' step in an agent's shown calculation.

    False if any step is wrong, True if at least one step was checkable and all were right, None if nothing
    could be safely evaluated (the claim then goes through evidence-based fact-checking as usual).

    Each '=' joins two sides. A side is checked only when at least one of them is an arithmetic expression:
    "a x b = c" (result on the right), "c = a x b" (result first), "a x b + c x d = e + f = g" (chained). Two bare
    numbers ("$6.1M per year = $508K per month") are a unit conversion, not arithmetic, and are skipped. A result is
    accepted to the precision it is written in ("about 2.3x" for 2.265) and as percentage points ("= 3.5 points").
    """
    checked = False
    for step in re.split(r"[;\n]|(?<=\d)\.\s", text):
        if "=" not in step:
            single = check_arithmetic(step)
            if single is False:
                return False
            checked = checked or single is True
            continue
        parts = step.split("=")
        for lhs, rhs in zip(parts, parts[1:]):
            left_expr, right_expr = _has_operator(lhs), _has_operator(rhs)
            if left_expr:
                expected = _evaluate(lhs)
                right = _evaluate(rhs) if right_expr else None
                if expected is None:
                    continue
                if right is not None:                       # chained: both sides are expressions
                    checked = True
                    if not _agrees(expected, right, rhs, "", lhs):
                        return False
                    continue
                stated = _first_number(rhs)
            elif right_expr:                                # result first: "21,264 = 886 x 24"
                expected, stated = _evaluate(rhs), _first_number(lhs)
                lhs = rhs
            else:
                continue
            if expected is None or stated is None:
                continue
            checked = True
            if not _agrees(expected, stated[0], stated[1], stated[2], lhs):
                return False
    return True if checked else None


# --- Calculations stated in words ("870 cities multiplied by 30% gives 261 cities") ------------------------------
_WORD_OPS = ((r"\b(?:multiplied by|times)\b", " x "), (r"\bdivided by\b", " / "), (r"\bminus\b", " - "), (r"\bplus\b", " + "),
             (r"\b(?:equals?|gives?|yields?|comes to|totals?|adds up to)\b", " = "))


def check_sentence_math(text: str) -> bool | None:
    """check_calculation for a claim written in words: "x multiplied by y gives z" is read as "x x y = z".

    Only a stated operation between two numbers counts ("cost equals $15,000" is not a calculation), so a wrong result
    can never be invented from prose that merely contains numbers.
    """
    plain = text
    for pattern, symbol in _WORD_OPS:
        plain = re.sub(pattern, symbol, plain, flags=re.I)
    if "=" not in plain or not re.search(r"\d[^=\d]*(?:\bx\b|[×*/+]|(?<=\s)[-−](?=\s))[^=]*\d", plain.split("=")[0]):
        return None
    return check_calculation(plain)
