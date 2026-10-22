"""One factory builds every persona node: personas differ in prompt and schema, not in mechanics."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from debate.agenda import AGENDA, FOLLOW_UP_ANSWER, OPENING_TOPIC
from debate.claims import extract_claims
from debate.config import CompanyConfig, ModelsConfig
from debate.guards import trim_to_words, word_count
from debate.numeric import check_calculation
from debate.llm.client import ModelClient
from debate.memory.views import MemoryView, vc_memories
from debate.prompts import build_system, prompt_version
from debate.rag.retriever import NullRetriever, Retrieval, Retriever, diversify
from debate.routing import SYSTEM as ROUTE_SYSTEM, announcement
from debate.state import DebateState, RouteChoice, Statement, Turn, Usage, VCQuestion

TIER = "debate"
LABEL = {"vc": "VC", "ceo": "CEO", "cfo": "CFO", "cto": "CTO", "devil": "DEVIL", "moderator": "MODERATOR", "assistant": "VC ASSISTANT", "panel": "the panel"}
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
    retriever: Retriever = field(default_factory=NullRetriever)
    sinks: list[Callable[[Turn], None]] = field(default_factory=list)
    retrieval_sinks: list[Callable[[int, str, Retrieval], None]] = field(default_factory=list)  # (turn seq, kind, result)
    event_sinks: list[Callable[[str, dict], None]] = field(default_factory=list)
    claim_sinks: list[Callable[[list[dict]], None]] = field(default_factory=list)
    call_sinks: list[Callable[[str, str, str, Usage], None]] = field(default_factory=list)
    vc_memory: MemoryView | None = None
    spent_usd: float = 0.0

    def call(self, role: str, tier_name: str, system: str, user: str, schema):
        """Every model call goes through here so the per-run budget cap covers all of them."""
        cap = self.cfg.limits.max_cost_usd_per_run
        if self.spent_usd >= cap:
            raise BudgetExceeded(f"run cost ${self.spent_usd:.4f} reached the ${cap:.2f} cap")
        parsed, usage = self.llm.generate(role=role, tier=self.cfg.tiers[tier_name], system=system, user=user, schema=schema)
        self.spent_usd += usage.cost_usd
        for sink in self.call_sinks:
            sink(role, tier_name, self.cfg.tiers[tier_name].model, usage)
        return parsed, usage

    def event(self, type_: str, payload: dict) -> None:
        for sink in self.event_sinks:
            sink(type_, payload)


NOTICE_SPEAKERS = ("moderator", "assistant")   # routing notices and "facts are correct/incorrect": not debate content


def last_said(state: DebateState) -> dict:
    """The most recent turn spoken by a debater (not a moderator or assistant notice)."""
    return next(t for t in reversed(state["turns"]) if t["speaker"] not in NOTICE_SPEAKERS)


MAX_ASSUMPTIONS = 3
MAX_ESTIMATES = 3      # "answer any three": independent sizing methods, each with its own maths
MAX_SUPPORT_WORDS = 40  # per calculation / assumption; the 60-word limit applies to the point itself


def support_lines(t: dict) -> list[str]:
    """The maths and assumptions behind a turn, as shown to the other agents and in exports."""
    lines = []
    if t.get("calculation"):
        flag = " (does NOT add up)" if t.get("calculation_ok") is False else ""
        lines.append(f"maths{flag}: {t['calculation']}")
    if t.get("assumptions"):
        lines.append("assumes: " + "; ".join(t["assumptions"]))
    for i, e in enumerate(t.get("estimates") or [], 1):
        flag = " (does NOT add up)" if e.get("calculation_ok") is False else ""
        line = f"estimate {i} - {e['method']}{flag}: {e['calculation']}"
        if e.get("assumptions"):
            line += " | assumes: " + "; ".join(e["assumptions"])
        lines.append(line)
    return lines


def format_transcript(turns: list[dict], keep: int) -> str:
    turns = [t for t in turns if t["speaker"] not in NOTICE_SPEAKERS]   # notices are for the record, not for the agents
    if not turns:
        return "(the meeting has just started)"
    lines = []
    for t in turns[-keep:]:
        lines.append(f"{LABEL[t['speaker']]} -> {LABEL.get(t['addressed_to'], t['addressed_to'])}: {t['content']}")
        lines.extend(f"    [{line}]" for line in support_lines(t))
    return "\n".join(lines)


def checked_text(parsed) -> str:
    """Everything the speaker said, labelled so the claim extractor treats maths as numbers and assumptions as
    assumptions: claims hidden in the maths or assumptions are fact-checked too, not just the point."""
    parts = [parsed.point]
    if getattr(parsed, "calculation", ""):
        parts.append(f"CALCULATION: {parsed.calculation}")
    parts += [f"ASSUMPTION: {a}" for a in getattr(parsed, "assumptions", [])]
    for e in getattr(parsed, "estimates", []):
        parts.append(f"ESTIMATE ({e.method}) CALCULATION: {e.calculation}")
        parts += [f"ASSUMPTION: {a}" for a in e.assumptions]
    return "\n".join(parts)


def _tidy_support(parsed) -> None:
    """Keep the shown maths and assumptions short; empty strings are dropped."""
    if hasattr(parsed, "calculation"):
        parsed.calculation = trim_to_words(parsed.calculation.strip(), MAX_SUPPORT_WORDS) if parsed.calculation.strip() else ""
        parsed.assumptions = [trim_to_words(a.strip(), MAX_SUPPORT_WORDS)
                              for a in parsed.assumptions if a.strip()][:MAX_ASSUMPTIONS]
    if hasattr(parsed, "estimates"):
        kept = []
        for e in parsed.estimates:
            if not e.calculation.strip():
                continue  # an estimate without maths is just a claim; it belongs in the point
            e.method = trim_to_words(e.method.strip() or "Estimate", 8)
            e.calculation = trim_to_words(e.calculation.strip(), MAX_SUPPORT_WORDS)
            e.assumptions = [trim_to_words(a.strip(), MAX_SUPPORT_WORDS) for a in e.assumptions if a.strip()][:MAX_ASSUMPTIONS]
            kept.append(e)
        parsed.estimates = kept[:MAX_ESTIMATES]


def evidence_for(deps: "Deps", query: str, k: int, prefer_topics: tuple[str, ...] = (),
                 topic_query: str = "") -> Retrieval:
    """What an agent gets to read: the top-ranked chunks, with assumptions and sizing models capped (diversify).

    When the question belongs to an agenda topic, half the slots go to the best chunks from that topic's corpus
    topics (e.g. a market-size question gets market figures, not just whatever ranks highest overall).
    """
    r = deps.retriever.search(query, deps.company.company_id, k * 5)
    ranked = r.chunks
    if prefer_topics:
        pool = deps.retriever.search(topic_query, deps.company.company_id, k * 5).chunks if topic_query else ranked
        preferred = diversify([c for c in pool if c.topic in prefer_topics], (k + 1) // 2)
        rest = [c for c in ranked if c not in preferred]
        ranked = preferred + rest
    return Retrieval(query=r.query, chunks=diversify(ranked, k))


def format_evidence(r: Retrieval) -> str:
    if not r.chunks:
        return "(no evidence retrieved)"
    def label(c) -> str:
        parts = [c.doc_type.replace("_", " ")]
        parts += [c.topic.replace("_", " ")] if c.topic else []
        parts += [f"p.{c.page}"] if c.page else []
        parts += [f"as of {c.as_of}"] if c.as_of else []
        return ", ".join(parts)

    return "\n\n".join(f"[{c.chunk_id}] ({label(c)})\n{c.text}" for c in r.chunks)


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


def _speak(deps: Deps, role: str, system: str, user: str, schema):
    """Generate a short, structured reply, then split it into claims for the fact-checker."""
    parsed, usage, retries, trimmed = _generate_short(deps, role, system, user, schema)
    _tidy_support(parsed)
    claims, claim_usage = extract_claims(deps, checked_text(parsed))
    return parsed, usage + claim_usage, retries, trimmed, claims, claim_usage.cost_usd


def _record(deps: Deps, state: DebateState, role: str, parsed, usage: Usage, retries: int, trimmed: bool,
            addressed_to: str, *, retrieval: Retrieval | None = None, claims=None,
            check_cost=0.0, topic: str = "", follow_up: bool = False) -> dict:
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
        claims=claims or [],
        check_cost_usd=check_cost,
        calculation=getattr(parsed, "calculation", ""),
        assumptions=list(getattr(parsed, "assumptions", [])),
        calculation_ok=check_calculation(parsed.calculation) if getattr(parsed, "calculation", "") else None,
        estimates=[{**e.model_dump(), "calculation_ok": check_calculation(e.calculation)}
                   for e in getattr(parsed, "estimates", [])],
        topic=topic,
        follow_up=follow_up,
    )
    if retrieval is not None:
        for sink in deps.retrieval_sinks:
            sink(turn.seq, "context", retrieval)
    for sink in deps.sinks:
        sink(turn)
    return turn.model_dump()


def vc_open_node(deps: Deps):
    def node(state: DebateState) -> dict:
        system = build_system("vc_opening", company=deps.company, max_words=deps.cfg.limits.max_words_per_turn)
        retrieval = evidence_for(deps, f"{deps.company.name} business customers market revenue",
                                 deps.cfg.limits.retrieval_k)
        parsed, usage, retries, trimmed, claims, check_cost = _speak(
            deps, "vc_opening", system,
            "Open the meeting.\n\nEVIDENCE:\n" + format_evidence(retrieval) +
            vc_memories(deps.vc_memory, deps.company.company_id, query=deps.company.name),
            VCQuestion)
        parsed.addressed_to = "ceo"  # the opening asks the CEO for the pitch, whatever the model said
        return {"turns": [_record(deps, state, "vc_opening", parsed, usage, retries, trimmed, "ceo",
                                  retrieval=retrieval, claims=claims, check_cost=check_cost)],
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


def _vc_plan(state: DebateState, max_rounds: int) -> tuple[str, str | None, str | None, bool]:
    """What the next VC question must do: (instruction, forced topic, forced executive, is_follow_up)."""
    pending = state.get("follow_up")
    covered = state.get("agenda_covered") or []
    remaining = [t for t in AGENDA.values() if t.key not in covered]
    if pending:
        topic = AGENDA.get(pending["topic"])
        who = LABEL[pending["exec"]]
        probe = topic.follow_up if topic else "Ask how they reached that answer and which assumption drives it."
        return (f"FOLLOW-UP to the {who}'s last answer{f' on {topic.title}' if topic else ''}. {probe} "
                f"Stay on this topic and address it to the {who}.",
                pending["topic"], pending["exec"], True)
    if any(t.key == OPENING_TOPIC for t in remaining):
        t = AGENDA[OPENING_TOPIC]
        return f"Next topic: {t.title}. {t.ask}", t.key, t.owner, False
    if remaining:
        menu = "\n".join(f"- {t.key}: {t.title} ({LABEL[t.owner]}) - {t.ask}" for t in remaining)
        return ("Open the most important topic not yet covered and set `topic` to its key:\n" + menu,
                None, None, False)
    return "Every agenda topic is covered: ask about the biggest uncertainty that is still unresolved.", None, None, False


def vc_question_node(deps: Deps):
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        n = state["round"] + 1
        instruction, topic, forced_exec, is_follow_up = _vc_plan(state, lim.max_rounds)
        last = " It is your last question: make it count." if n == lim.max_rounds else ""
        # The plan goes in the user message, not the system prompt, so each role's system prompt stays cacheable.
        system = build_system("vc", company=deps.company, max_words=lim.max_words_per_turn)
        facts = ""
        if state.get("fact_note"):
            facts = f"\n\nFACT-CHECK of the last exchange:\n{state['fact_note']}"
            cred = credibility(state["claims"])
            if cred:
                facts += f"\nSupported claims so far: {cred}."
        retrieval = evidence_for(deps, last_said(state)["content"], lim.retrieval_k)
        user = (f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}{facts}"
                f"{vc_memories(deps.vc_memory, deps.company.company_id, query=last_said(state)['content'])}"
                f"\n\nEVIDENCE:\n{format_evidence(retrieval)}"
                f"\n\nThis is question {n} of {lim.max_rounds}.{last}\n{instruction}")
        parsed, usage, retries, trimmed, claims, check_cost = _speak(deps, "vc", system, user, VCQuestion)

        covered = list(state.get("agenda_covered") or [])
        if not is_follow_up:
            remaining = [k for k in AGENDA if k not in covered]
            if topic is None:  # the VC chose; fall back to the next uncovered topic if it picked badly
                topic = parsed.topic if parsed.topic in remaining else (remaining[0] if remaining else None)
            if topic:
                covered.append(topic)
        # Who answers is the router's decision (route_node), which also schedules the follow-up.
        return {"turns": [_record(deps, state, "vc", parsed, usage, retries, trimmed, "panel",
                                  retrieval=retrieval, claims=claims, check_cost=check_cost,
                                  topic=topic or "", follow_up=is_follow_up)],
                "agenda_covered": covered}
    return node


def route_node(deps: Deps):
    """Decide who takes the VC's question and say so in the conversation. Follow-ups skip the model."""
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        q = next(t for t in reversed(state["turns"]) if t["speaker"] == "vc")
        pending = state.get("follow_up")
        if q.get("follow_up") and pending:
            executive, usage, model, tier, reason = pending["exec"], Usage(), "code", "code", "follow-up"
            upcoming = None
        else:
            agenda = AGENDA.get(q.get("topic") or "")
            asked = (f"Agenda topic: {agenda.title}\n" if agenda else "") + f"Question: {q['content']}"
            parsed, usage = deps.call("route", "route", ROUTE_SYSTEM, asked, RouteChoice)
            executive, model, tier, reason = parsed.executive, deps.cfg.tiers["route"].model, "route", parsed.reason
            # Every main question earns a follow-up to whoever answers it, if a round is left for it.
            upcoming = ({"topic": q["topic"], "exec": executive}
                        if q.get("topic") and state["round"] + 1 < lim.max_rounds else None)
            deps.event("route_decision", {"executive": executive, "model": model, "reason": reason,
                                          "confidence": parsed.confidence, "question_seq": q["seq"]})
        turn = Turn(seq=len(state["turns"]) + 1, round=state["round"] + 1, speaker="moderator", addressed_to=executive,
                    content=announcement(executive, bool(q.get("follow_up"))), model=model, tier=tier, usage=usage,
                    prompt_version=prompt_version(), topic=q.get("topic", ""), follow_up=bool(q.get("follow_up")))
        for sink in deps.sinks:
            sink(turn)
        return {"turns": [turn.model_dump()], "next_speaker": executive, "follow_up": upcoming}
    return node


def _argue(deps: Deps, state: DebateState, role: str, task: str, query: str, addressed_to: str,
           topic: str = "", follow_up: bool = False) -> dict:
    lim = deps.cfg.limits
    system = build_system(role, company=deps.company, max_words=lim.max_words_per_turn)
    agenda = AGENDA.get(topic)
    retrieval = evidence_for(deps, f"{query} {ROLE_HINT[role]}".strip(), lim.retrieval_k,
                             agenda.corpus_topics if agenda else (), agenda.evidence_query if agenda else "")
    user = (f"Meeting so far:\n{format_transcript(state['turns'], lim.context_turns)}\n\n"
            f"EVIDENCE:\n{format_evidence(retrieval)}\n\n{task}")
    parsed, usage, retries, trimmed, claims, check_cost = _speak(deps, role, system, user, Statement)
    rec = _record(deps, state, role, parsed, usage, retries, trimmed, addressed_to, retrieval=retrieval,
                  claims=claims, check_cost=check_cost, topic=topic, follow_up=follow_up)
    return rec


def executive_node(deps: Deps, role: str):
    def node(state: DebateState) -> dict:
        q = next(t for t in reversed(state["turns"]) if t["speaker"] == "vc")
        topic = AGENDA.get(q.get("topic") or "")
        task = "Answer the VC's latest question."
        if q.get("follow_up"):
            task += " " + FOLLOW_UP_ANSWER
        elif topic:
            task += " " + topic.answer
        query = f"{q['content']} {topic.title if topic else ''}".strip()
        return {"turns": [_argue(deps, state, role, task, query, "vc",
                                 topic=q.get("topic", ""), follow_up=bool(q.get("follow_up")))]}
    return node


def devil_node(deps: Deps):
    def node(state: DebateState) -> dict:
        last = state["turns"][-1]  # the executive who just answered
        task = ("Attack the weakest point of the last answer. If it gave estimates or maths, go after the weakest "
                "estimate, input or assumption specifically.")
        rec = _argue(deps, state, "devil", task, last["content"], last["speaker"],
                     topic=last.get("topic", ""), follow_up=bool(last.get("follow_up")))
        return {"turns": [rec], "round": state["round"] + 1}
    return node
