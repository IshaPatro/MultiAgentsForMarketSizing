"""Model client interface. Agents depend on this, never on a vendor SDK, so the System-1 model
(JEV, Phase 4) can be added as another implementation without touching any agent code."""
from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from debate.config import ModelsConfig, TierConfig
from debate.state import Usage

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model call finished but did not produce a usable structured reply."""


class ModelClient(Protocol):
    def generate(
        self, *, role: str, tier: TierConfig, system: str, user: str, schema: type[T]
    ) -> tuple[T, Usage]: ...


def compute_cost(cfg: ModelsConfig, model: str, usage: Usage) -> float:
    """USD from real token counts; prices are per million tokens."""
    p = cfg.prices[model]
    return (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    ) / 1_000_000
