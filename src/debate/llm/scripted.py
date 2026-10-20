"""Offline client for tests and `--dry-run`: no network, no API key, deterministic output."""
from __future__ import annotations

from typing import Callable, TypeVar

from pydantic import BaseModel

from debate.config import ModelsConfig, TierConfig
from debate.llm.client import compute_cost
from debate.state import Usage, VCQuestion

T = TypeVar("T", bound=BaseModel)

Responder = Callable[[str, str, str, type[BaseModel], int], BaseModel]
_EXECS = ("ceo", "cfo", "cto")


def default_responder(role: str, system: str, user: str, schema: type[BaseModel], call_no: int) -> BaseModel:
    point = f"[dry-run {role}] Placeholder argument number {call_no} that stays well under the word limit."
    if schema is VCQuestion:
        return VCQuestion(point=point, addressed_to=_EXECS[call_no % len(_EXECS)])
    return schema(point=point)


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
