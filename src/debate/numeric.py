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
