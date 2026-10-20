"""Anachronism guard + claim extraction (implementation.md sections 9 and 10).

Two layers. Deterministic: era-specific forbidden terms block a turn with no model call. Model-based: one
System-1 call splits the turn into atomic claims AND says whether it presupposes post-cutoff knowledge; years
after the cutoff are passed as a hint because plans for "2020" are fine in 2017 but "in 2020 we saw" is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from debate.config import EraContext
from debate.rag.dating import later_years
from debate.state import ClaimExtraction, ExtractedClaim, Usage

EXTRACTION_SYSTEM = (
    "You audit one statement from a simulated meeting set on {cutoff}. Nobody in the meeting can know anything "
    "that happened after {cutoff}.\n"
    "1. Split the statement into atomic claims (each self-contained) and label each: fact, number, forecast or opinion. "
    "Claims about what someone said or did NOT say in this meeting are opinion, not fact.\n"
    "2. Decide `relies_on_later_knowledge`: true only if the statement states or presupposes events or facts "
    "from after {cutoff} (hindsight). Predictions, plans and hypotheticals about the future are NOT hindsight."
)


def forbidden_hits(text: str, era: EraContext) -> list[str]:
    return [pat for pat in era.forbidden_terms if re.search(pat, text, re.I)]


def is_era_safe(text: str, era: EraContext, *, allow_later_years: bool = True) -> bool:
    """Deterministic screen for text that reaches an agent without passing the full turn check.

    Model-written notes (e.g. fact-check notes) can carry the model's own hindsight, so they are screened with
    allow_later_years=False: a checker note has no reason to mention a year after the cutoff.
    """
    if forbidden_hits(text, era):
        return False
    return allow_later_years or not later_years(text, era.cutoff_date)


@dataclass
class TurnCheck:
    claims: list[ExtractedClaim] = field(default_factory=list)
    blocked_reason: str | None = None
    usage: Usage = field(default_factory=Usage)

    @property
    def blocked(self) -> bool:
        return self.blocked_reason is not None


def check_turn(deps, text: str) -> TurnCheck:
    era = deps.era
    hits = forbidden_hits(text, era)
    if hits:
        return TurnCheck(blocked_reason=f"uses terms that only exist after {era.cutoff_date}: {', '.join(hits)}")
    years = later_years(text, era.cutoff_date)
    hint = f"\n\nYears after the cutoff that appear in the statement: {years}." if years else ""
    parsed, usage = deps.call("claim_extract", "fast", EXTRACTION_SYSTEM.format(cutoff=era.cutoff_date),
                              f"Statement:\n{text}{hint}", ClaimExtraction)
    reason = (parsed.later_knowledge_reason or "relies on later knowledge") if parsed.relies_on_later_knowledge else None
    return TurnCheck(claims=parsed.claims, blocked_reason=reason, usage=usage)
