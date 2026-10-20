"""Fact-checker (implementation.md section 10): claims from BOTH sides, System 1 first, escalate when unsure."""
from __future__ import annotations

from debate.anachronism import is_era_safe
from debate.numeric import check_arithmetic
from debate.nodes.agents import LABEL, Deps, credibility
from debate.rag.retriever import Retrieval
from debate.state import DebateState, VerdictBatch

CHECKED_SPEAKERS = ("vc", "ceo", "cfo", "cto", "devil")
VERIFY_TYPES = ("fact", "number")

SYSTEM = (
    "You are a neutral fact-checker in a simulated meeting set on {cutoff}. For each numbered claim you get the "
    "most relevant EVIDENCE chunks. Judge ONLY against that evidence:\n"
    "- supported: the evidence states it or clearly implies it.\n"
    "- contradicted: the evidence states something incompatible (a different number, date or fact).\n"
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
            if t["seq"] <= state["checked_through"] or t["speaker"] not in CHECKED_SPEAKERS or t["blocked"]:
                continue
            for i, c in enumerate(t["claims"], 1):
                rec = {"claim_id": f"{state['run_id']}-t{t['seq']}-c{i}", "turn_seq": t["seq"], "speaker": t["speaker"],
                       "text": c["text"], "type": c["type"], "verdict": "not_checked", "confidence": None,
                       "evidence_ids": [], "note": "", "escalated": False, "checker_model": ""}
                records.append(rec)
                if c["type"] in VERIFY_TYPES:
                    if c["type"] == "number" and check_arithmetic(c["text"]) is False:
                        rec.update(verdict="miscalculated", confidence=1.0,
                                   note="Arithmetic does not match the stated result", checker_model="python")
                        continue
                    r: Retrieval = deps.retriever.search(c["text"], deps.era, deps.company.company_id, lim.claim_evidence_k)
                    for sink in deps.retrieval_sinks:
                        sink(t["seq"], "factcheck", r)
                    if not r.chunks:
                        rec.update(verdict="unverifiable", confidence=1.0,
                                   note="No eligible evidence", checker_model="python")
                        continue
                    items.append({"ref": f"C{len(items) + 1}", "rec": rec, "speaker": t["speaker"], "text": c["text"], "retrieval": r})

        spent_before = deps.spent_usd
        if items:
            system = SYSTEM.format(cutoff=deps.era.cutoff_date)
            first, _ = deps.call("fact_check", "fast", system, _prompt(items), VerdictBatch)
            verdicts = _verdict_map(first, items)
            fast_model = deps.cfg.tiers["fast"].model
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
        return {"claims": records, "checked_through": through, "fact_note": _note(records, deps.era)}
    return node


def _note(new: list[dict], era) -> str:
    checked = [c for c in new if c["verdict"] in ("supported", "contradicted", "unverifiable", "miscalculated")]
    if not checked:
        return ""
    counts = {v: sum(c["verdict"] == v for c in checked)
              for v in ("supported", "contradicted", "unverifiable", "miscalculated")}
    lines = [f"Checked {len(checked)} claims: {counts['supported']} supported, {counts['contradicted']} contradicted, "
             f"{counts['unverifiable']} unverifiable, {counts['miscalculated']} miscalculated."]
    for c in checked:
        if c["verdict"] != "supported":
            # Claim text comes from a turn that passed the guard; the note is free text from the checker model
            # and could carry its own hindsight, so it is screened before the VC can read it.
            text = c["text"] if is_era_safe(c["text"], era) else "(claim text withheld)"
            note = c["note"] if is_era_safe(c["note"], era, allow_later_years=False) else "note withheld"
            lines.append(f"- {LABEL[c['speaker']]}: \"{text}\" is {c['verdict']} ({note})")
    return "\n".join(lines)
