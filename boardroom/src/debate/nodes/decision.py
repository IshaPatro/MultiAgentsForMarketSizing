"""Final investment judgment."""
from __future__ import annotations

import json

from debate.memory.views import vc_memories
from debate.nodes.agents import Deps, credibility, format_transcript
from debate.state import DebateState, VCDecision


def decision_node(deps: Deps):
    def node(state: DebateState) -> dict:
        claims = state["claims"]
        valid_ids = {c["claim_id"] for c in claims if c["verdict"] == "supported" and c["evidence_ids"]}
        system = (
            f"You are the VC making the final investment decision on {deps.company.name}. "
            "Score team, market, product, traction, moat, timing and business model from 1 to 5. "
            "For every score include a score_drivers entry with a short reason and supported claim IDs. "
            "Use only supported claims with evidence IDs as established facts. Treat forecasts, opinions, "
            "unverifiable and contradicted claims as uncertainty. Cite only supported claim IDs supplied below. "
            "Give concrete reasons for and against and an actionable condition if relevant."
        )
        user = (f"Meeting:\n{format_transcript(state['turns'], len(state['turns']))}\n\n"
                f"Credibility: {credibility(claims)}\n\n"
                f"Checked claims (ID, speaker, verdict, evidence IDs, text):\n{json.dumps(claims)}"
                f"{vc_memories(deps.vc_memory, deps.company.company_id, query=deps.company.name)}")
        parsed, _ = deps.call("vc_decision", "decision", system, user, VCDecision)
        parsed.cited_claim_ids = list(dict.fromkeys(cid for cid in parsed.cited_claim_ids if cid in valid_ids))
        for driver in parsed.score_drivers.__dict__.values():
            driver.claim_ids = [cid for cid in driver.claim_ids if cid in valid_ids]
        return {"decision": parsed.model_dump()}
    return node
