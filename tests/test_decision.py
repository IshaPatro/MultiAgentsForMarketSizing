import pytest
from conftest import FakeRetriever, chunk, make_run
from pydantic import ValidationError

from debate.llm.scripted import default_responder
from debate.state import ClaimVerdict, ScoreDrivers, VCDecision, VerdictBatch


def test_every_score_driver_is_required():
    with pytest.raises(ValidationError):
        ScoreDrivers.model_validate({})


def test_the_decision_can_only_cite_supported_claims_with_evidence(cfg):
    def responder(role, system, user, schema, n):
        if schema is VerdictBatch:
            refs = [r for r in ("C1", "C2", "C3") if r in user]
            return VerdictBatch(verdicts=[ClaimVerdict(ref=r, verdict="supported", confidence=0.95,
                                                       evidence_ids=["c1"], note="ok") for r in refs])
        if schema is VCDecision:
            d = default_responder(role, system, user, schema, n)
            d.cited_claim_ids = ["t-t2-c1", "invented-id"]
            d.score_drivers.market.claim_ids = ["invented-id", "t-t2-c1"]
            return d
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=1, retriever=FakeRetriever([chunk("c1")]))
    decision = state["decision"]
    assert decision["cited_claim_ids"] == ["t-t2-c1"]
    assert decision["score_drivers"]["market"]["claim_ids"] == ["t-t2-c1"]
    assert "era" not in decision
