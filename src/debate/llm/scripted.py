"""Offline client for tests and `--dry-run`: no network, no API key, deterministic output."""
from __future__ import annotations

from typing import Callable, TypeVar

from pydantic import BaseModel

from debate.config import ModelsConfig, TierConfig
from debate.llm.client import compute_cost
import re

from debate.state import ClaimExtraction, ClaimVerdict, ExtractedClaim, RouteChoice, Usage, VCQuestion, VerdictBatch, VCDecision, VCScores, ScoreDriver, ScoreDrivers

T = TypeVar("T", bound=BaseModel)

Responder = Callable[[str, str, str, type[BaseModel], int], BaseModel]
_EXECS = ("ceo", "cfo", "cto")


def default_responder(role: str, system: str, user: str, schema: type[BaseModel], call_no: int) -> BaseModel:
    if schema is VCDecision:
        return VCDecision(decision="pass", conviction=0.6,
                          scores=VCScores(team=3, market=2, product=3, traction=3, moat=2, timing=2, business_model=2),
                          score_drivers=ScoreDrivers(**{k: ScoreDriver(reason="Simulated score", claim_ids=[])
                                                        for k in VCScores.model_fields}),
                          key_reasons_for=["Simulated reason"], key_reasons_against=["Simulated risk"],
                          deal_terms_sketch="Simulation only", what_would_change_my_mind=["Verified traction"],
                          cited_claim_ids=[])
    if schema is RouteChoice:   # the agenda topic's owner, like a sensible router
        from debate.agenda import AGENDA
        owner = next((t.owner for t in AGENDA.values() if f"Agenda topic: {t.title}\n" in user), "ceo")
        return RouteChoice(executive=owner, reason="dry-run", confidence=0.9)
    if schema is ClaimExtraction:
        return ClaimExtraction(claims=[ExtractedClaim(text="Placeholder checkable claim.", type="fact")])
    if schema is VerdictBatch:
        refs = dict.fromkeys(re.findall(r"^(C\d+) ", user, re.M))
        return VerdictBatch(verdicts=[ClaimVerdict(ref=r, verdict="supported", confidence=0.9, evidence_ids=[],
                                                   note="dry-run") for r in refs])
    point = f"[dry-run {role}] Placeholder argument number {call_no} that stays well under the word limit."
    if schema is VCQuestion:
        return VCQuestion(point=point, addressed_to=_EXECS[call_no % len(_EXECS)])
    if role == "cfo" and "THREE independent sizing approaches" in user:
        from debate.state import Estimate, Statement
        return Statement(point=f"[dry-run cfo] Three sizing methods give $0.5B to $6.5B; I would defend the top-down one.",
                         estimates=[
                             Estimate(method="Beachhead budgets (top-down)",
                                      calculation="$110B x 20% = $22B; $22B x 4.6% = $1.012B; $1.012B x 50% = $506M",
                                      assumptions=["4.6% of public health goes to recovery services (Boston ratio)",
                                                   "50% capture across four cities"]),
                             Estimate(method="Share of an existing market (top-down)",
                                      calculation="$138B x 53.9% = $74.38B; $74.38B x 1% = $743.8M",
                                      assumptions=["municipal share stays at the 2015 ratio", "1% capture"]),
                             Estimate(method="Manhole sampling (bottom-up)",
                                      calculation="7.75B people x 70% / 10,000 = 542,500 sites; 542,500 x 12 = "
                                                  "6,510,000 samples; 6,510,000 x $1,000 = $6.51B",
                                      assumptions=["10,000 people per catchment", "monthly sampling", "$1,000 per sample"]),
                         ])
    # Exercise the real citation path when an offline run has RAG enabled.
    evidence = user.split("EVIDENCE:\n", 1)[-1] if "EVIDENCE:\n" in user else ""
    cited = re.search(r"^\[([A-Za-z0-9_-]+)\]", evidence, re.M)
    return schema(point=point, evidence_ids=[cited.group(1)] if cited else [])


class ScriptedClient:
    def __init__(self, cfg: ModelsConfig, responder: Responder = default_responder):
        self.cfg = cfg
        self.responder = responder
        self.calls: list[dict] = []

    def generate(
        self, *, role: str, tier: TierConfig, system: str, user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        call_no = len(self.calls) + 1
        parsed = self.responder(role, system, user, schema, call_no)
        self.calls.append({"role": role, "system": system, "user": user, "tier": tier.model})
        usage = Usage(
            input_tokens=(len(system) + len(user)) // 4,
            output_tokens=len(parsed.model_dump_json()) // 4,
        )
        usage.cost_usd = compute_cost(self.cfg, tier.model, usage)
        return parsed, usage  # type: ignore[return-value]
