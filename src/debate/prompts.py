"""Persona prompts live in prompts/*.md so they can be versioned and diffed."""
from __future__ import annotations

import hashlib

from debate.config import ROOT, CompanyConfig, EraContext

PROMPT_DIR = ROOT / "prompts"
ROLES = ("vc_opening", "vc", "ceo", "cfo", "cto", "devil")


def prompt_version() -> str:
    """Short hash over every prompt file, stored on each turn for reproducibility."""
    h = hashlib.sha256()
    for path in sorted(PROMPT_DIR.glob("*.md")):
        h.update(path.read_bytes())
    return h.hexdigest()[:8]


def build_system(role: str, *, company: CompanyConfig, era: EraContext, max_words: int, **extra: str) -> str:
    """Role prompt first, then the shared rules + briefing. Stable per (role, era) so it is cacheable."""
    if role not in ROLES:
        raise KeyError(f"unknown role {role!r}")
    summary = company.summaries.get(era.name)
    if summary is None:
        raise KeyError(f"{company.company_id} has no briefing for era {era.name!r} yet")
    values = {
        "company_name": company.name,
        "cutoff_date": era.cutoff_date.isoformat(),
        "era_label": era.label,
        "max_words": max_words,
        "summary": summary.strip(),
        "round_instruction": "",
        **extra,
    }
    role_text = (PROMPT_DIR / f"{role}.md").read_text()
    common = (PROMPT_DIR / "_common.md").read_text()
    return (role_text.strip() + "\n\n" + common.strip()).format(**values)
