"""Shared data shapes: what the model returns, what a stored turn looks like, graph state."""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Executive = Literal["ceo", "cfo", "cto"]


AgendaTopic = Literal["market_size", "customers", "unit_economics", "technology_moat", "go_to_market",
                      "icp_beachhead", "competition", "founders_team", "vision", "fundraise", "use_of_funds", "risks"]


class Estimate(BaseModel):
    """One independent way of sizing something (e.g. a market), with its own maths and assumptions."""

    method: str = Field(description="Short name, e.g. 'Beachhead budgets (top-down)'.")
    calculation: str = Field(
        description="Step by step with units, one 'expression = result' per step separated by ';', ending in "
        "this estimate's result (e.g. '$110B x 20% = $22B; $22B x 4.6% = $1.0B; $1.0B x 50% = $506M')."
    )
    assumptions: list[str] = Field(default_factory=list, description="Every assumption behind this estimate.")


class Statement(BaseModel):
    """Structured reply for CEO / CFO / CTO / Devil's Advocate."""

    point: str = Field(description="Your answer: precise and complete, specifics first, no preamble or filler.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Ids of the EVIDENCE chunks this answer relies on. Empty if it is opinion or the evidence is silent.",
    )
    calculation: str = Field(
        default="",
        description="If the point uses a number you derived, the arithmetic step by step with units, one "
        "'expression = result' per step separated by ';' (e.g. '4 cities x $110B x 20% = $88B'). Empty if none.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Every assumption the answer depends on that the evidence does not state "
        "(e.g. 'a city pays $50k per year'). Empty if the point rests only on evidence.",
    )
    estimates: list[Estimate] = Field(
        default_factory=list,
        description="Only when asked to size a market or compare methods: up to 3 independent approaches, "
        "each with its own maths and assumptions. Empty otherwise.",
    )


class VCQuestion(BaseModel):
    """Structured reply for the VC: a question to the whole team (the moderator decides who answers)."""

    point: str = Field(description="Your question to the team, precise and specific. Do not name who should answer. "
                                   "If ready_to_decide is true, a one-sentence closing remark instead.")
    topic: AgendaTopic | None = Field(default=None, description="The agenda topic this question opens.")
    follow_up: bool = Field(default=False, description="True to keep probing the topic just discussed.")
    ready_to_decide: bool = Field(default=False,
                                  description="True only when every agenda topic is covered and you have what you need.")
    evidence_ids: list[str] = Field(default_factory=list,
                                    description="Retrieved chunk IDs supporting any factual premise in the question.")


class RouteChoice(BaseModel):
    """Router reply: which executive takes the VC's question."""

    executive: Executive
    reason: str = Field(default="", description="At most 12 words.")
    confidence: float | None = Field(default=None, description="0 to 1, when the router can tell.")


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


class ClaimVerdict(BaseModel):
    ref: str = Field(description="The claim reference, e.g. C1.")
    verdict: Verdict
    confidence: float = Field(description="0 to 1: how sure you are of this verdict.")
    evidence_ids: list[str] = Field(description="Chunk ids that decided it (empty if unverifiable).")
    note: str = Field(description="At most 15 words.")


class VerdictBatch(BaseModel):
    verdicts: list[ClaimVerdict]


class VCScores(BaseModel):
    team: int = Field(ge=1, le=5)
    market: int = Field(ge=1, le=5)
    product: int = Field(ge=1, le=5)
    traction: int = Field(ge=1, le=5)
    moat: int = Field(ge=1, le=5)
    timing: int = Field(ge=1, le=5)
    business_model: int = Field(ge=1, le=5)


class ScoreDriver(BaseModel):
    reason: str = Field(description="Why this category received its score, including uncertainty.")
    claim_ids: list[str] = Field(description="Supported claim IDs that informed the score; empty if none.")


class ScoreDrivers(BaseModel):
    team: ScoreDriver
    market: ScoreDriver
    product: ScoreDriver
    traction: ScoreDriver
    moat: ScoreDriver
    timing: ScoreDriver
    business_model: ScoreDriver


class VCDecision(BaseModel):
    decision: Literal["invest", "pass", "invest_with_conditions"]
    conviction: float = Field(ge=0, le=1)
    scores: VCScores
    score_drivers: ScoreDrivers
    key_reasons_for: list[str]
    key_reasons_against: list[str]
    deal_terms_sketch: str
    what_would_change_my_mind: list[str]
    cited_claim_ids: list[str]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    requests: int = 0   # API requests behind this usage: 1 per Claude call; a JEV verdict batch makes one per claim

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
    check_cost_usd: float = 0.0                             # claim-extraction call, included in usage
    calculation: str = ""                                   # arithmetic the speaker showed for the point
    assumptions: list[str] = Field(default_factory=list)    # premises the evidence does not state
    calculation_ok: bool | None = None                      # Python re-check; None = nothing checkable
    estimates: list[dict] = Field(default_factory=list)     # Estimate dicts + their own calculation_ok
    topic: str = ""                                         # agenda topic of the exchange
    follow_up: bool = False                                 # True for the VC's "how did you get there?" probe


class DebateState(TypedDict):
    run_id: str
    company_id: str
    round: int  # completed question/answer/attack cycles
    next_speaker: str  # executive picked by the VC
    turns: Annotated[list[dict], operator.add]  # append-only; Turn.model_dump() dicts
    claims: Annotated[list[dict], operator.add]  # one record per extracted claim, with its verdict
    checked_through: int  # highest turn seq already fact-checked
    fact_note: str  # short summary of the latest fact-check, shown to the VC
    decision: dict | None
    agenda_covered: list[str]  # agenda topics already opened by a main VC question
    follow_up: dict | None  # {"topic", "exec"} the next VC question must probe, else None
    current: dict | None  # {"topic", "exec"} of the latest main question, for VC-chosen follow-ups
    vc_ready: bool  # the VC has what it needs: go to the decision
