"""Fact-checker (implementation.md section 10): claims from BOTH sides, System 1 first, escalate when unsure."""
from __future__ import annotations

import re

from debate.numeric import check_calculation, check_sentence_math
from debate.nodes.agents import LABEL, Deps, credibility
from debate.rag.retriever import Retrieval
from debate.prompts import prompt_version
from debate.state import DebateState, Turn, Usage, VerdictBatch

CHECKED_SPEAKERS = ("vc", "ceo", "cfo", "cto", "devil")
# Assumptions and sizing models restate inputs that are not established by the case, and public leads are
# unverified screenshot transcriptions, so none can be the evidence that makes a claim "supported". Filtered in
# code, not left to the checker prompt.
NOT_EVIDENCE = ("assumption", "sizing_model", "public_lead")
VERIFY_TYPES = ("fact", "number")
_MEETING_CLAIM = re.compile(
    r"^the\s+(?:ceo|cfo|cto|vc|devil|speaker)(?:'s|\b).*\b"
    r"(?:says?|said|cites?|offers?|argues?|concedes?|claims?|asked|fallback|defense|plan|business rests)\b",
    re.I,
)


def is_meeting_claim(text: str) -> bool:
    """Characterizations of the debate are opinions; source retrieval cannot verify them."""
    return bool(_MEETING_CLAIM.search(text))

SYSTEM = (
    "You are a neutral fact-checker in a simulated venture-capital meeting. For each numbered claim you get the "
    "most relevant EVIDENCE chunks. Judge ONLY against that evidence:\n"
    "- A SYNTHETIC SCENARIO GROUND TRUTH chunk is authoritative for the hypothetical boardroom scenario only. "
    "It does not verify a claim about real-world Biobot outside that scenario.\n"
    "- supported: the evidence states it or clearly implies it.\n"
    "- contradicted: the evidence states something incompatible (a different number, date or fact). "
    "If the evidence gives an exact date for the same event, a different asserted date is contradicted.\n"
    "- unverifiable: the evidence does not cover it. Missing evidence is NOT a contradiction.\n"
    "Give a confidence from 0 to 1, the chunk ids that decided it, and a note of at most 15 words. "
    "Return one verdict per claim, using its reference (C1, C2, ...)."
)


def _prompt(items: list[dict]) -> str:
    parts = []
    for it in items:
        ev = "\n".join(f"  [{c.chunk_id}] {c.text}" for c in it["retrieval"].chunks) or "  (no evidence found)"
        parts.append(f"{it['ref']} ({LABEL[it['speaker']]}): {it['text']}\nEVIDENCE:\n{ev}")
    return "\n\n".join(parts)


def _verdict_map(batch: VerdictBatch, items: list[dict]) -> dict[str, dict]:
    out = {}
    for v in batch.verdicts:
        item = next((i for i in items if i["ref"] == v.ref), None)
        if item is None:
            continue
        allowed = set(item["retrieval"].ids)
        citations = list(dict.fromkeys(e for e in v.evidence_ids if e in allowed))
        if v.verdict in ("supported", "contradicted") and not citations:
            out[v.ref] = {"verdict": "unverifiable", "confidence": 0.0, "evidence_ids": [],
                          "note": "No valid supporting citation"}
        else:
            out[v.ref] = {"verdict": v.verdict, "confidence": max(0.0, min(1.0, v.confidence)),
                          "evidence_ids": citations, "note": v.note}
    return out


def fact_check_node(deps: Deps):
    def node(state: DebateState) -> dict:
        lim = deps.cfg.limits
        through = max((t["seq"] for t in state["turns"]), default=0)
        items, records = [], []
        for t in state["turns"]:
            if t["seq"] <= state["checked_through"] or t["speaker"] not in CHECKED_SPEAKERS:
                continue
            if t.get("calculation") and t.get("calculation_ok") is False:
                records.append({"claim_id": f"{state['run_id']}-t{t['seq']}-calc", "turn_seq": t["seq"],
                                "speaker": t["speaker"], "text": t["calculation"], "type": "number",
                                "verdict": "miscalculated", "confidence": 1.0, "evidence_ids": [],
                                "note": "Shown calculation does not add up", "escalated": False,
                                "checker_model": "python"})
            for k, e in enumerate(t.get("estimates") or [], 1):
                if e.get("calculation_ok") is False:
                    records.append({"claim_id": f"{state['run_id']}-t{t['seq']}-est{k}", "turn_seq": t["seq"],
                                    "speaker": t["speaker"], "text": f"{e['method']}: {e['calculation']}",
                                    "type": "number", "verdict": "miscalculated", "confidence": 1.0,
                                    "evidence_ids": [], "note": "Estimate's calculation does not add up",
                                    "escalated": False, "checker_model": "python"})
            for i, c in enumerate(t["claims"], 1):
                rec = {"claim_id": f"{state['run_id']}-t{t['seq']}-c{i}", "turn_seq": t["seq"], "speaker": t["speaker"],
                       "text": c["text"], "type": "opinion" if is_meeting_claim(c["text"]) else c["type"],
                       "verdict": "not_checked", "confidence": None,
                       "evidence_ids": [], "note": "", "escalated": False, "checker_model": ""}
                records.append(rec)
                if rec["type"] in VERIFY_TYPES:
                    if rec["type"] == "number":
                        math = check_calculation(c["text"])   # whole expressions, not fragments
                        if math is None:
                            math = check_sentence_math(c["text"])
                        if math is False:
                            rec.update(verdict="miscalculated", confidence=1.0,
                                       note="Arithmetic does not match the stated result", checker_model="python")
                            continue
                        if math is True:   # a calculation, not a corpus fact: code settles it, no model call
                            rec.update(type="calculation", verdict="supported", confidence=1.0, checker_model="python",
                                       note="Arithmetic verified in code; its inputs are the speaker's own")
                            continue
                    r: Retrieval = deps.retriever.search(c["text"], deps.company.company_id, lim.claim_evidence_k + 2)
                    # The speaker's accepted citations are also eligible evidence. Hybrid search can miss a
                    # relevant chunk for a paraphrased atomic claim, so preserve those exact dated chunks.
                    by_ids = getattr(deps.retriever, "by_ids", None)
                    if by_ids:
                        cited = by_ids(t.get("evidence_ids", []), deps.company.company_id)
                        seen = {chunk.chunk_id for chunk in cited}
                        r.chunks = cited + [chunk for chunk in r.chunks if chunk.chunk_id not in seen]
                    r.chunks = [ch for ch in r.chunks if ch.doc_type not in NOT_EVIDENCE][:lim.claim_evidence_k + len(t.get("evidence_ids", []))]
                    for sink in deps.retrieval_sinks:
                        sink(t["seq"], "factcheck", r)
                    if not r.chunks:
                        rec.update(verdict="unverifiable", confidence=1.0,
                                   note="No eligible evidence", checker_model="python")
                        continue
                    items.append({"ref": f"C{len(items) + 1}", "rec": rec, "speaker": t["speaker"], "text": c["text"], "retrieval": r})

        spent_before = deps.spent_usd
        if items:
            system = SYSTEM
            tier = "verdict" if "verdict" in deps.cfg.tiers else "fast"   # runs saved before the tier existed
            first, _ = deps.call("fact_check", tier, system, _prompt(items), VerdictBatch)
            verdicts = _verdict_map(first, items)
            fast_model = deps.cfg.tiers[tier].model
            for it in items:  # a claim the model skipped stays unverifiable at zero confidence, so it escalates
                v = verdicts.setdefault(it["ref"], {"verdict": "unverifiable", "confidence": 0.0, "evidence_ids": [],
                                                    "note": "no verdict returned"})
                it["rec"].update(v, checker_model=fast_model)
            # Escalate when unsure, and when about to accuse: a false "contradicted" damages a side unfairly.
            hard = [it for it in items if it["rec"]["confidence"] < lim.escalate_below or it["rec"]["verdict"] == "contradicted"]
            if hard:
                hard_model = deps.cfg.tiers["check_hard"].model
                second, _ = deps.call("fact_check_hard", "check_hard", system, _prompt(hard), VerdictBatch)
                for ref, v in _verdict_map(second, hard).items():
                    it = next(i for i in hard if i["ref"] == ref)
                    it["rec"].update(v, checker_model=hard_model, escalated=True)

        if items:  # fact-check calls belong to no single turn, so their cost is logged as an event
            deps.event("fact_check_cost", {"claims": len(items), "cost_usd": deps.spent_usd - spent_before})
        if records:
            for sink in deps.claim_sinks:
                sink(records)
        # The VC's assistant reports only whether the facts held up; the detail goes to the VC through `fact_note`.
        wrong = any(r["verdict"] in ("contradicted", "miscalculated") for r in records)
        spoken = next((t for t in reversed(state["turns"]) if t["speaker"] != "moderator"), None)
        tier = "verdict" if "verdict" in deps.cfg.tiers else "fast"
        verdict_turn = Turn(seq=len(state["turns"]) + 1, round=state["round"], speaker="assistant", addressed_to="vc",
                            content="Facts are incorrect." if wrong else "Facts are correct.",
                            model=deps.cfg.tiers[tier].model, tier=tier, usage=Usage(), prompt_version=prompt_version(),
                            topic=(spoken or {}).get("topic", ""), follow_up=bool((spoken or {}).get("follow_up")))
        for sink in deps.sinks:
            sink(verdict_turn)
        return {"turns": [verdict_turn.model_dump()], "claims": records, "checked_through": through,
                "fact_note": _note(records)}
    return node


def _note(new: list[dict]) -> str:
    checked = [c for c in new if c["verdict"] in ("supported", "contradicted", "unverifiable", "miscalculated")]
    if not checked:
        return ""
    counts = {v: sum(c["verdict"] == v for c in checked)
              for v in ("supported", "contradicted", "unverifiable", "miscalculated")}
    lines = [f"Checked {len(checked)} claims: {counts['supported']} supported, {counts['contradicted']} contradicted, "
             f"{counts['unverifiable']} unverifiable, {counts['miscalculated']} miscalculated."]
    for c in checked:
        if c["verdict"] != "supported":
            lines.append(f"- {LABEL[c['speaker']]}: \"{c['text']}\" is {c['verdict']} ({c['note']})")
    return "\n".join(lines)
