"""Shared data shapes: what the model returns, what a stored turn looks like, graph state."""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Executive = Literal["ceo", "cfo", "cto"]


class Statement(BaseModel):
    """Structured reply for CEO / CFO / CTO / Devil's Advocate: exactly one point."""

    point: str = Field(description="The single point, at most 60 words, no preamble.")


class VCQuestion(BaseModel):
    """Structured reply for the VC: one question and who should answer it."""

    point: str = Field(description="One sharp question, at most 60 words.")
    addressed_to: Executive = Field(description="Which executive should answer.")


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


class DebateState(TypedDict):
    run_id: str
    company_id: str
    era: dict
    round: int  # completed question/answer/attack cycles
    next_speaker: str  # executive picked by the VC
    turns: Annotated[list[dict], operator.add]  # append-only; Turn.model_dump() dicts
