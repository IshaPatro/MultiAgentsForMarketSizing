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


def check_arithmetic(text: str) -> bool | None:
    """Return True/False for a clear equation, or None when there is no safe calculation."""
    percent = _PERCENT.search(text)
    if percent:
        expected = Decimal(percent.group(1)) * _value(percent.group(2)) / 100
        stated = _value(percent.group(3))
    else:
        times = _TIMES.search(text)
        if not times:
            return None
        expected = _value(times.group(1)) * _value(times.group(2))
        stated = _value(times.group(3))
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


def check_calculation(text: str) -> bool | None:
    """Check every 'expression = result' step in an agent's shown calculation.

    False if any step is wrong, True if at least one step was checkable and all were right, None if nothing
    could be safely evaluated (the claim then goes through evidence-based fact-checking as usual).
    """
    checked = False
    for step in re.split(r"[;\n]|(?<=\d)\.\s", text):
        if "=" not in step:
            single = check_arithmetic(step)
            if single is False:
                return False
            checked = checked or single is True
            continue
        parts = [p for p in step.split("=")]
        for lhs, rhs in zip(parts, parts[1:]):
            expected = _evaluate(lhs)
            stated = next((m.group("num") for m in _TOKEN.finditer(rhs.replace("~", "").replace("≈", ""))
                           if m.group("num")), None)
            if expected is None or stated is None:
                continue
            value = _amount(stated)
            checked = True
            if abs(expected - value) > max(Decimal("0.005"), abs(expected) * _STEP_TOLERANCE):
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
