"""Typed loaders for configs/*.yaml. Nothing else in the package reads YAML directly."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


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
    retrieval_k: int = 4
    claim_evidence_k: int = 3
    escalate_below: float = 0.75


class ModelsConfig(BaseModel):
    tiers: dict[str, TierConfig]
    prices: dict[str, Price]
    limits: Limits = Field(default_factory=Limits)


class CompanyConfig(BaseModel):
    company_id: str
    name: str
    summary: str  # one or two sentences of context for every system prompt


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


DATE_PATTERN = r"^\d{4}(-\d{2}(-\d{2})?)?$"  # YYYY, YYYY-MM or YYYY-MM-DD: as precise as the source allows


class CaseSource(BaseModel):
    path: str
    title: str
    published: str = Field(pattern=DATE_PATTERN)
    as_of: str = Field(pattern=DATE_PATTERN)          # the date the case narrative describes
    narrative_pages: tuple[int, int]
    caption_pages: tuple[int, int] | None = None
    endnote_pages: tuple[int, int] | None = None


class ExhibitSpec(BaseModel):
    id: str
    number: int
    page: int
    layout: Literal["matrix", "budget"]
    topic: str
    as_of: str = Field(pattern=DATE_PATTERN)
    header_lines: int = 2                            # matrix: how many rows form each block's column headings
    groups: list[str] = Field(default_factory=list)  # matrix: a label per block, e.g. public / private sector
    columns: dict[str, str] = Field(default_factory=dict)  # budget: csv column -> heading in the exhibit


class PublicSection(BaseModel):
    topic: str
    reliability: Literal["sourced", "lead"] = "sourced"   # lead: unverified transcription, never evidence


class PublicSource(BaseModel):
    id: str
    path: str
    title: str
    checked: str = Field(pattern=DATE_PATTERN)            # when the research was checked; no item may be later
    sections: dict[str, PublicSection]
    item_topics: dict[str, str] = Field(default_factory=dict)
    item_dates: dict[str, str] = Field(default_factory=dict)


class CuratedFiles(BaseModel):
    facts: str
    assumptions: str
    sizing: str


class CorpusManifest(BaseModel):
    company_id: str
    corpus_dir: str
    case: CaseSource
    topics: dict[str, str]
    section_topics: dict[str, str]
    paragraph_topics: dict[str, str] = Field(default_factory=dict)
    exhibits: list[ExhibitSpec] = Field(default_factory=list)
    caption_as_of: dict[int, str] = Field(default_factory=dict)
    endnote_dates: dict[str, str] = Field(default_factory=dict)
    public_sources: list[PublicSource] = Field(default_factory=list)
    curated: CuratedFiles

    def model_post_init(self, _context) -> None:
        known = set(self.topics)
        used = {*self.section_topics.values(), *self.paragraph_topics.values(), *(e.topic for e in self.exhibits),
                *(s.topic for p in self.public_sources for s in p.sections.values()),
                *(t for p in self.public_sources for t in p.item_topics.values())}
        if used - known:
            raise ValueError(f"manifest uses undeclared topics: {sorted(used - known)}")


def load_corpus(company_id: str, root: Path = ROOT) -> CorpusManifest:
    path = root / "configs" / "corpus" / f"{company_id}.yaml"
    if not path.exists():
        raise KeyError(f"no corpus manifest at {path}")
    return CorpusManifest(**_yaml(path))


def load_company(company_id: str, root: Path = ROOT) -> CompanyConfig:
    path = root / "configs" / "companies" / f"{company_id}.yaml"
    if not path.exists():
        raise KeyError(f"no company config at {path}")
    return CompanyConfig(**_yaml(path))
