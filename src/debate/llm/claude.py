"""System-2 client: Claude through the official Anthropic SDK with structured outputs."""
from __future__ import annotations

import time
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from debate.config import ModelsConfig, TierConfig
from debate.llm.client import LLMError, compute_cost
from debate.state import Usage

T = TypeVar("T", bound=BaseModel)


class ClaudeClient:
    def __init__(self, cfg: ModelsConfig, client: anthropic.Anthropic | None = None):
        self.cfg = cfg
        # Zero-arg constructor resolves ANTHROPIC_API_KEY or an `ant auth login` profile.
        self.client = client or anthropic.Anthropic()

    def generate(
        self, *, role: str, tier: TierConfig, system: str, user: str, schema: type[T]
    ) -> tuple[T, Usage]:
        started = time.monotonic()
        response = self.client.messages.parse(
            model=tier.model,
            max_tokens=tier.max_tokens,
            # The persona prompt is frozen text at the front of the request; cache_control makes it
            # reusable across turns once it clears the model's minimum cacheable length.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": tier.effort},
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.stop_reason == "refusal":
            raise LLMError(f"{role}: model refused ({response.stop_details})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{role}: hit max_tokens={tier.max_tokens} before finishing the reply")
        if response.parsed_output is None:
            raise LLMError(f"{role}: no structured output (stop_reason={response.stop_reason})")

        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
        )
        usage.cost_usd = compute_cost(self.cfg, tier.model, usage)
        return response.parsed_output, usage
