"""Shared data shapes: what the model returns, what a stored turn looks like, graph state."""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Executive = Literal["ceo", "cfo", "cto"]


class Statement(BaseModel):
    """Structured reply for CEO / CFO / CTO / Devil's Advocate: exactly one point."""

    point: str = Field(description="The single point, at most 60 words, no preamble.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the EVIDENCE chunks this point relies on (max 2). Empty if it is opinion or the evidence is silent.",
    )


class VCQuestion(BaseModel):
    """Structured reply for the VC: one question and who should answer it."""

    point: str = Field(description="One sharp question, at most 60 words.")
    addressed_to: Executive = Field(description="Which executive should answer.")


ClaimType = Literal["fact", "number", "forecast", "opinion"]
# What the checker model may return. "miscalculated" is deliberately absent: only the Python arithmetic check
# can issue it, so a model cannot make an unverified, never-escalated accusation.
Verdict = Literal["supported", "contradicted", "unverifiable"]


class ExtractedClaim(BaseModel):
    text: str = Field(description="One self-contained claim, rewritten so it makes sense on its own.")
    type: ClaimType = Field(
        description="fact = checkable statement about the past/present; number = a figure or calculation; "
        "forecast = prediction or plan; opinion = judgement, boast or rhetoric."
    )


class ClaimExtraction(BaseModel):
    claims: list[ExtractedClaim]
    relies_on_later_knowledge: bool = Field(
        description="True only if the statement states or presupposes events/facts after the cutoff date. "
        "Forecasts and plans about the future are allowed."
    )
    later_knowledge_reason: str = Field(description="Empty string if false; otherwise one short sentence.")


class ClaimVerdict(BaseModel):
    ref: str = Field(description="The claim reference, e.g. C1.")
    verdict: Verdict
    confidence: float = Field(description="0 to 1: how sure you are of this verdict.")
    evidence_ids: list[str] = Field(description="Chunk ids that decided it (empty if unverifiable).")
    note: str = Field(description="At most 15 words.")


class VerdictBatch(BaseModel):
    verdicts: list[ClaimVerdict]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(**{k: getattr(self, k) + getattr(other, k) for k in Usage.model_fields})


class Turn(BaseModel):
    seq: int
    round: int
    speaker: str
    addressed_to: str
    content: str
    model: str
    tier: str
    usage: Usage
    retries: int = 0
    trimmed: bool = False
    prompt_version: str = ""
    evidence_ids: list[str] = Field(default_factory=list)   # citations that were actually retrieved
    invalid_citations: int = 0                              # cited ids that were not in the retrieved set
    claims: list[ExtractedClaim] = Field(default_factory=list)
    blocked: bool = False                                   # withheld: still anachronistic after regeneration
    regenerations: int = 0
    check_cost_usd: float = 0.0                             # extraction/anachronism calls, included in usage


class DebateState(TypedDict):
    run_id: str
    company_id: str
    era: dict
    round: int  # completed question/answer/attack cycles
    next_speaker: str  # executive picked by the VC
    turns: Annotated[list[dict], operator.add]  # append-only; Turn.model_dump() dicts
    claims: Annotated[list[dict], operator.add]  # one record per extracted claim, with its verdict
    checked_through: int  # highest turn seq already fact-checked
    fact_note: str  # short summary of the latest fact-check, shown to the VC
