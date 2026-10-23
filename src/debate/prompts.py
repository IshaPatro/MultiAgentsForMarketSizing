"""Persona prompts live in prompts/*.md so they can be versioned and diffed."""
from __future__ import annotations

import hashlib

from debate.config import ROOT, CompanyConfig

PROMPT_DIR = ROOT / "prompts"
ROLES = ("vc_opening", "vc", "ceo", "cfo", "cto", "devil")


def prompt_version() -> str:
    """Short hash over role prompts and inline checker/decision prompts."""
    h = hashlib.sha256()
    for path in sorted(PROMPT_DIR.glob("*.md")):
        h.update(path.read_bytes())
    for relative in ("src/debate/claims.py", "src/debate/nodes/factcheck.py", "src/debate/nodes/decision.py"):
        h.update((ROOT / relative).read_bytes())
    return h.hexdigest()[:8]


def build_system(role: str, *, company: CompanyConfig, **extra: str) -> str:
    """Role prompt first, then the shared rules + company context. Stable per role, so it is cacheable."""
    if role not in ROLES:
        raise KeyError(f"unknown role {role!r}")
    values = {
        "company_name": company.name,
        "summary": company.summary.strip(),
        "round_instruction": "",
        **extra,
    }
    role_text = (PROMPT_DIR / f"{role}.md").read_text()
    common = (PROMPT_DIR / "_common.md").read_text()
    return (role_text.strip() + "\n\n" + common.strip()).format(**values)
