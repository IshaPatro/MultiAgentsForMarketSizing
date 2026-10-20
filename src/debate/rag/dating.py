"""Assign each chunk the date its information was knowable (implementation.md section 7).

Fail closed: a chunk that mentions a year after the baseline and cannot be judged gets no date, and an
undated chunk is never retrievable (`as_of_date <= cutoff` is not true for NULL).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, Field

from debate.config import TierConfig
from debate.llm.client import ModelClient

YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


class DateJudgement(BaseModel):
    describes_later_events: bool = Field(
        description="True only if the text states that something HAPPENED after the baseline date. "
        "Forecasts, projections and planned dates written before the baseline are False."
    )
    latest_event_year: int | None = Field(description="Year of the latest event described, if any.")
    reason: str = Field(description="One short sentence.")


@dataclass
class DateDecision:
    as_of: date | None
    needs_review: bool
    note: str = ""


def later_years(text: str, baseline: date) -> list[int]:
    return sorted({int(y) for y in YEAR.findall(text) if int(y) > baseline.year})


def date_chunk(text: str, baseline: date, llm: ModelClient | None = None, tier: TierConfig | None = None) -> DateDecision:
    years = later_years(text, baseline)
    if not years:
        return DateDecision(baseline, False)
    if llm is None or tier is None:
        return DateDecision(None, True, f"mentions {years} after {baseline}; no date check available, left undated")
    system = (
        f"You date passages from a document that describes a situation as of {baseline.isoformat()}. "
        "Decide whether the passage describes events that happened AFTER that date, or whether later years "
        "only appear as forecasts, projections or plans made before it."
    )
    j, _ = llm.generate(role="date_check", tier=tier, system=system,
                        user=f"Years after the baseline mentioned: {years}\n\nPassage:\n{text}", schema=DateJudgement)
    if not j.describes_later_events:
        return DateDecision(baseline, True, f"later years {years} are projections ({j.reason}); confirm in review CSV")
    year = j.latest_event_year if j.latest_event_year and j.latest_event_year > baseline.year else max(years)
    return DateDecision(date(year, 12, 31), True, f"describes events up to {year} ({j.reason})")
