"""Route first-pass fact-check verdicts to JEV (TypeSafe System One) when configured, otherwise Haiku."""
from __future__ import annotations

from debate.config import ModelsConfig, Price
from debate.llm.client import ModelClient


class ModelRouter:
    def __init__(self, claude: ModelClient, jev: ModelClient | None = None):
        self.claude, self.jev = claude, jev

    def generate(self, *, role, tier, system, user, schema):
        if tier.model.startswith("jev:"):
            if self.jev is None:
                raise RuntimeError("JEV tier configured without a JEV client (is JEV_API_KEY set?)")
            return self.jev.generate(role=role, tier=tier, system=system, user=user, schema=schema)
        return self.claude.generate(role=role, tier=tier, system=system, user=user, schema=schema)


def configure_profile(cfg: ModelsConfig, profile: str, jev_model: str | None = None) -> ModelsConfig:
    """Mutate a freshly loaded config for one run; the Opus profile is a comparison baseline."""
    if profile == "opus_low":
        for name in ("debate", "fast", "verdict", "route", "check_hard"):
            cfg.tiers[name].model = "claude-opus-5-5"
            cfg.tiers[name].effort = "low"
    elif profile == "routed":
        if jev_model:   # JEV classifies; claim extraction (`fast`) is generative and stays on Haiku
            cfg.tiers["verdict"].model = cfg.tiers["route"].model = f"jev:{jev_model}"
            cfg.prices[f"jev:{jev_model}"] = Price(input=0.042, output=0, cache_read=0, cache_write=0)  # JEV: $0.042 per million input tokens, output free
    else:
        raise ValueError(f"unknown model profile: {profile}")
    return cfg
