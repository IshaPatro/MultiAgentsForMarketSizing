"""One factory builds every persona node: personas differ in prompt and schema, not in mechanics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from debate.config import CompanyConfig, EraContext, ModelsConfig
from debate.guards import trim_to_words, word_count
from debate.llm.client import ModelClient
from debate.prompts import build_system, prompt_version
from debate.state import DebateState, Statement, Turn, Usage, VCQuestion

TIER = "debate"
LABEL = {"vc": "VC", "ceo": "CEO", "cfo": "CFO", "cto": "CTO", "devil": "DEVIL"}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Deps:
    llm: ModelClient
    cfg: ModelsConfig
    company: CompanyConfig
    era: EraContext
    sinks: list[Callable[[Turn], None]] = field(default_factory=list)
    spent_usd: float = 0.0


def format_transcript(turns: list[dict], keep: int) -> str:
    if not turns:
        return "(the meeting has just started)"
    lines = []
    for t in turns[-keep:]:
        lines.append(f"{LABEL[t['speaker']]} -> {LABEL.get(t['addressed_to'], t['addressed_to'])}: {t['content']}")
    return "\n".join(lines)


def _generate_short(deps: Deps, role: str, system: str, user: str, schema):
    """Call the model; if the reply is over the word limit, ask once for a rewrite, then hard-trim."""
    lim = deps.cfg.limits
    tier = deps.cfg.tiers[TIER]
    total, retries = Usage(), 0
    while True:
        if deps.spent_usd >= lim.max_cost_usd_per_run:
            raise BudgetExceeded(f"run cost ${deps.spent_usd:.4f} reached the ${lim.max_cost_usd_per_run:.2f} cap")
        parsed, usage = deps.llm.generate(role=role, tier=tier, system=system, user=user, schema=schema)
        deps.spent_usd += usage.cost_usd
        total = total + usage
        n = word_count(parsed.point)
        if n <= lim.max_words_per_turn or retries >= lim.max_brevity_retries:
            break
        retries += 1
        user = (
            f"{user}\n\nYour previous reply was {n} words. Rewrite it in at most "
            f"{lim.max_words_per_turn} words, keeping only the single strongest point."
        )
    trimmed = word_count(parsed.point) > lim.max_words_per_turn
    if trimmed:
        parsed.point = trim_to_words(parsed.point, lim.max_words_per_turn)
    return parsed, total, retries, trimmed


def _record(deps: Deps, state: DebateState, role: str, parsed, usage: Usage, retries: int, trimmed: bool,
            addressed_to: str) -> dict:
    turn = Turn(
        seq=len(state["turns"]) + 1,
        round=state["round"] + 1,
        speaker=role if role != "vc_opening" else "vc",
        addressed_to=addressed_to,
        content=parsed.point.strip(),
        model=deps.cfg.tiers[TIER].model,
        tier="system2",
        usage=usage,
        retries=retries,
        trimmed=trimmed,
        prompt_version=prompt_version(),
    )
    for sink in deps.sinks:
        sink(turn)
    return turn.model_dump()


def vc_open_node(deps: Deps):
    def node(state: DebateState) -> dict:
        system = build_system("vc_opening", company=deps.company, era=deps.era,
                              max_words=deps.cfg.limits.max_words_per_turn)
        parsed, usage, retries, trimmed = _generate_short(
            deps, "vc_opening", system, "Open the meeting.", VCQuestion)
        parsed.addressed_to = "ceo"  # the opening asks the CEO for the pitch, whatever the model said
        return {"turns": [_record(deps, state, "vc_opening", parsed, usage, retries, trimmed, "ceo")],
                "next_speaker": "ceo"}
    return node


def vc_question_node(deps: Deps):
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        n = state["round"] + 1
        note = f"This is question {n} of {lim.max_rounds}." + (" It is your last question: make it count." if n == lim.max_rounds else "")
        system = build_system("vc", company=deps.company, era=deps.era, max_words=lim.max_words_per_turn,
                              round_instruction=note)
        user = f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}\n\nAsk your next question."
        parsed, usage, retries, trimmed = _generate_short(deps, "vc", system, user, VCQuestion)
        return {"turns": [_record(deps, state, "vc", parsed, usage, retries, trimmed, parsed.addressed_to)],
                "next_speaker": parsed.addressed_to}
    return node


def executive_node(deps: Deps, role: str):
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        system = build_system(role, company=deps.company, era=deps.era, max_words=lim.max_words_per_turn)
        user = f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}\n\nAnswer the VC's latest question."
        parsed, usage, retries, trimmed = _generate_short(deps, role, system, user, Statement)
        return {"turns": [_record(deps, state, role, parsed, usage, retries, trimmed, "vc")]}
    return node


def devil_node(deps: Deps):
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        system = build_system("devil", company=deps.company, era=deps.era, max_words=lim.max_words_per_turn)
        user = f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}\n\nAttack the weakest point of the last answer."
        parsed, usage, retries, trimmed = _generate_short(deps, "devil", system, user, Statement)
        target = state["turns"][-1]["speaker"]  # the executive who just answered
        return {"turns": [_record(deps, state, "devil", parsed, usage, retries, trimmed, target)],
                "round": state["round"] + 1}
    return node
