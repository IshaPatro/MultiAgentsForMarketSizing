"""One factory builds every persona node: personas differ in prompt and schema, not in mechanics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from debate.anachronism import check_turn
from debate.config import CompanyConfig, EraContext, ModelsConfig
from debate.guards import trim_to_words, word_count
from debate.llm.client import ModelClient
from debate.prompts import build_system, prompt_version
from debate.rag.retriever import NullRetriever, Retrieval, Retriever
from debate.state import DebateState, Statement, Turn, Usage, VCQuestion

TIER = "debate"
LABEL = {"vc": "VC", "ceo": "CEO", "cfo": "CFO", "cto": "CTO", "devil": "DEVIL"}
ROLE_HINT = {  # nudges retrieval toward what each executive is accountable for
    "ceo": "market customers team vision", "cfo": "budget pricing revenue cost funding",
    "cto": "technology hardware data intellectual property", "devil": "",
}


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Deps:
    llm: ModelClient
    cfg: ModelsConfig
    company: CompanyConfig
    era: EraContext
    retriever: Retriever = field(default_factory=NullRetriever)
    sinks: list[Callable[[Turn], None]] = field(default_factory=list)
    retrieval_sinks: list[Callable[[int, str, Retrieval], None]] = field(default_factory=list)  # (turn seq, kind, result)
    event_sinks: list[Callable[[str, dict], None]] = field(default_factory=list)
    claim_sinks: list[Callable[[list[dict]], None]] = field(default_factory=list)
    spent_usd: float = 0.0

    def call(self, role: str, tier_name: str, system: str, user: str, schema):
        """Every model call goes through here so the per-run budget cap covers all of them."""
        cap = self.cfg.limits.max_cost_usd_per_run
        if self.spent_usd >= cap:
            raise BudgetExceeded(f"run cost ${self.spent_usd:.4f} reached the ${cap:.2f} cap")
        parsed, usage = self.llm.generate(role=role, tier=self.cfg.tiers[tier_name], system=system, user=user, schema=schema)
        self.spent_usd += usage.cost_usd
        return parsed, usage

    def event(self, type_: str, payload: dict) -> None:
        for sink in self.event_sinks:
            sink(type_, payload)


def format_transcript(turns: list[dict], keep: int) -> str:
    if not turns:
        return "(the meeting has just started)"
    lines = []
    for t in turns[-keep:]:
        lines.append(f"{LABEL[t['speaker']]} -> {LABEL.get(t['addressed_to'], t['addressed_to'])}: {t['content']}")
    return "\n".join(lines)


def format_evidence(r: Retrieval) -> str:
    if not r.chunks:
        return "(no evidence retrieved)"
    return "\n\n".join(f"[{c.chunk_id}] (p.{c.page}, {c.section or c.doc_type})\n{c.text}" for c in r.chunks)


def _generate_short(deps: Deps, role: str, system: str, user: str, schema):
    """Call the model; if the reply is over the word limit, ask once for a rewrite, then hard-trim."""
    lim = deps.cfg.limits
    total, retries = Usage(), 0
    while True:
        parsed, usage = deps.call(role, TIER, system, user, schema)
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


def _speak_checked(deps: Deps, role: str, system: str, user: str, schema, *, retrieval: Retrieval | None = None):
    """Generate -> anachronism check -> regenerate with a correction -> withhold if it keeps failing."""
    lim = deps.cfg.limits
    base_user, total, check_cost, tries = user, Usage(), 0.0, 0
    parsed = retries = trimmed = check = None
    for attempt in range(lim.max_regenerations + 1):
        parsed, usage, retries, trimmed = _generate_short(deps, role, system, user, schema)
        check = check_turn(deps, parsed.point)
        total = total + usage + check.usage
        check_cost += check.usage.cost_usd
        if not check.blocked:
            break
        tries += 1
        deps.event("anachronism_block", {"speaker": role, "attempt": attempt + 1, "reason": check.blocked_reason,
                                         "text": parsed.point})
        # Deliberately generic: the checker's reason (and any matched term) is itself hindsight, and quoting it
        # back would plant the very knowledge we are trying to keep out of the agent's context.
        user = (f"{base_user}\n\nYour previous reply was rejected because it relied on information from after "
                f"{deps.era.cutoff_date}. Rewrite it using only the evidence above and what was said in the meeting.")
    blocked = check.blocked
    if blocked:
        deps.event("turn_withheld", {"speaker": role, "reason": check.blocked_reason})
        parsed = type(parsed)(**{**parsed.model_dump(),
                                 "point": f"(withheld: it relied on information after {deps.era.cutoff_date})",
                                 "evidence_ids": []})
    return parsed, total, retries, trimmed, check, blocked, tries, check_cost


def _record(deps: Deps, state: DebateState, role: str, parsed, usage: Usage, retries: int, trimmed: bool,
            addressed_to: str, *, retrieval: Retrieval | None = None, check=None, blocked=False,
            regenerations=0, check_cost=0.0) -> dict:
    cited = list(dict.fromkeys(getattr(parsed, "evidence_ids", [])))
    allowed = set(retrieval.ids) if retrieval else set()
    valid = [e for e in cited if e in allowed]
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
        evidence_ids=valid,
        invalid_citations=len(cited) - len(valid),
        claims=[] if blocked or check is None else check.claims,
        blocked=blocked,
        regenerations=regenerations,
        check_cost_usd=check_cost,
    )
    if retrieval is not None:
        for sink in deps.retrieval_sinks:
            sink(turn.seq, "context", retrieval)
    for sink in deps.sinks:
        sink(turn)
    return turn.model_dump()


def vc_open_node(deps: Deps):
    def node(state: DebateState) -> dict:
        system = build_system("vc_opening", company=deps.company, era=deps.era,
                              max_words=deps.cfg.limits.max_words_per_turn)
        parsed, usage, retries, trimmed, check, blocked, regens, check_cost = _speak_checked(
            deps, "vc_opening", system, "Open the meeting.", VCQuestion)
        parsed.addressed_to = "ceo"  # the opening asks the CEO for the pitch, whatever the model said
        return {"turns": [_record(deps, state, "vc_opening", parsed, usage, retries, trimmed, "ceo",
                                  check=check, blocked=blocked, regenerations=regens, check_cost=check_cost)],
                "next_speaker": "ceo"}
    return node


def credibility(claims: list[dict]) -> str:
    """Per-speaker share of checked claims that were supported, e.g. 'CEO 3/4, DEVIL 1/3'."""
    tally: dict[str, list[int]] = {}
    for c in claims:
        if c["verdict"] in ("supported", "contradicted", "unverifiable", "miscalculated"):
            t = tally.setdefault(c["speaker"], [0, 0])
            t[1] += 1
            t[0] += c["verdict"] == "supported"
    return ", ".join(f"{LABEL[s]} {a}/{b}" for s, (a, b) in tally.items())


def vc_question_node(deps: Deps):
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        n = state["round"] + 1
        note = f"This is question {n} of {lim.max_rounds}." + (" It is your last question: make it count." if n == lim.max_rounds else "")
        system = build_system("vc", company=deps.company, era=deps.era, max_words=lim.max_words_per_turn,
                              round_instruction=note)
        facts = ""
        if state.get("fact_note"):
            facts = f"\n\nFACT-CHECK of the last exchange:\n{state['fact_note']}"
            cred = credibility(state["claims"])
            if cred:
                facts += f"\nSupported claims so far: {cred}."
        user = (f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}{facts}"
                "\n\nAsk your next question.")
        parsed, usage, retries, trimmed, check, blocked, regens, check_cost = _speak_checked(
            deps, "vc", system, user, VCQuestion)
        return {"turns": [_record(deps, state, "vc", parsed, usage, retries, trimmed, parsed.addressed_to,
                                  check=check, blocked=blocked, regenerations=regens, check_cost=check_cost)],
                "next_speaker": parsed.addressed_to}
    return node


def _argue(deps: Deps, state: DebateState, role: str, task: str, query: str, addressed_to: str) -> dict:
    lim = deps.cfg.limits
    system = build_system(role, company=deps.company, era=deps.era, max_words=lim.max_words_per_turn)
    retrieval = deps.retriever.search(f"{query} {ROLE_HINT[role]}".strip(), deps.era, deps.company.company_id, lim.retrieval_k)
    user = (f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}\n\n"
            f"EVIDENCE:\n{format_evidence(retrieval)}\n\n{task}")
    parsed, usage, retries, trimmed, check, blocked, regens, check_cost = _speak_checked(
        deps, role, system, user, Statement, retrieval=retrieval)
    rec = _record(deps, state, role, parsed, usage, retries, trimmed, addressed_to, retrieval=retrieval,
                  check=check, blocked=blocked, regenerations=regens, check_cost=check_cost)
    return rec


def executive_node(deps: Deps, role: str):
    def node(state: DebateState) -> dict:
        question = state["turns"][-1]["content"]
        return {"turns": [_argue(deps, state, role, "Answer the VC's latest question.", question, "vc")]}
    return node


def devil_node(deps: Deps):
    def node(state: DebateState) -> dict:
        last = state["turns"][-1]  # the executive who just answered
        rec = _argue(deps, state, "devil", "Attack the weakest point of the last answer.", last["content"], last["speaker"])
        return {"turns": [rec], "round": state["round"] + 1}
    return node
