"""Typed loaders for configs/*.yaml. Nothing else in the package reads YAML directly."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


class EraContext(BaseModel, frozen=True):
    """The simulated 'today'. Every layer receives this; nothing may use a later date."""

    name: str
    cutoff_date: date
    description: str = ""

    @property
    def label(self) -> str:
        return self.name.replace("_", "-")


class TierConfig(BaseModel):
    model: str
    effort: str
    max_tokens: int


class Price(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


class Limits(BaseModel):
    max_words_per_turn: int = 60
    max_rounds: int = 3
    max_brevity_retries: int = 1
    max_cost_usd_per_run: float = 1.0
    context_turns: int = 6


class ModelsConfig(BaseModel):
    tiers: dict[str, TierConfig]
    prices: dict[str, Price]
    limits: Limits = Field(default_factory=Limits)


class CompanyConfig(BaseModel):
    company_id: str
    name: str
    briefs: dict[str, str]


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader; real environment variables win. Values are never printed."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def load_models(root: Path = ROOT) -> ModelsConfig:
    return ModelsConfig(**_yaml(root / "configs" / "models.yaml"))


def load_era(name: str, root: Path = ROOT) -> EraContext:
    eras = _yaml(root / "configs" / "eras.yaml")
    if name not in eras:
        raise KeyError(f"unknown era {name!r}; known: {', '.join(eras)}")
    return EraContext(name=name, **eras[name])


def load_company(company_id: str, root: Path = ROOT) -> CompanyConfig:
    path = root / "configs" / "companies" / f"{company_id}.yaml"
    if not path.exists():
        raise KeyError(f"no company config at {path}")
    return CompanyConfig(**_yaml(path))
