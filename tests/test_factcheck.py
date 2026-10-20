from conftest import FakeRetriever, calls_for, chunk, make_run

from debate.llm.scripted import default_responder
from debate.nodes.agents import credibility
from debate.state import ClaimExtraction, ClaimVerdict, ExtractedClaim, Statement, VCQuestion, VerdictBatch


def claims_responder(verdict_fast, verdict_hard=None):
    """CEO states one fact, one number, one forecast and one opinion; the devil states nothing checkable."""
    def responder(role, system, user, schema, n):
        if schema is ClaimExtraction:
            if "Cities will pay" in user:
                return ClaimExtraction(claims=[
                    ExtractedClaim(text="Boston has 50,427 manholes.", type="fact"),
                    ExtractedClaim(text="Four cities have a $110B budget.", type="number"),
                    ExtractedClaim(text="We will sign 100 cities by 2020.", type="forecast"),
                    ExtractedClaim(text="We are the best team.", type="opinion")],
                    relies_on_later_knowledge=False, later_knowledge_reason="")
            return ClaimExtraction(claims=[], relies_on_later_knowledge=False, later_knowledge_reason="")
        if role == "ceo" and schema is Statement:
            return Statement(point="Cities will pay for this.", evidence_ids=["c1", "bogus_id"])
        if schema is VerdictBatch:
            v = verdict_hard if role == "fact_check_hard" else verdict_fast
            return VerdictBatch(verdicts=[ClaimVerdict(ref=r, verdict=v[0], confidence=v[1], evidence_ids=["c1", "zzz"], note="n")
                                          for r in ("C1", "C2") if r in user])
        return default_responder(role, system, user, schema, n)
    return responder


def run(cfg, fast, hard=None, rounds=2):
    return make_run(cfg, claims_responder(fast, hard), rounds=rounds, retriever=FakeRetriever([chunk("c1")]))


def test_only_facts_and_numbers_are_verified_and_recorded_with_ids(cfg):
    state, llm, deps = run(cfg, ("supported", 0.95))
    ceo_claims = [c for c in state["claims"] if c["speaker"] == "ceo"]
    assert [c["type"] for c in ceo_claims] == ["fact", "number", "forecast", "opinion"]
    assert [c["verdict"] for c in ceo_claims] == ["supported", "supported", "not_checked", "not_checked"]
    assert len({c["claim_id"] for c in state["claims"]}) == len(state["claims"])
    assert ceo_claims[0]["evidence_ids"] == ["c1"]                         # ids not retrieved are dropped
    assert len(calls_for(llm, "fact_check_hard")) == 0                      # confident + supported: no escalation


def test_low_confidence_escalates_and_the_stronger_verdict_wins(cfg):
    state, llm, _ = run(cfg, ("supported", 0.5), ("contradicted", 0.9))
    c = next(c for c in state["claims"] if c["speaker"] == "ceo")
    assert c["escalated"] and c["verdict"] == "contradicted" and c["checker_model"] == cfg.tiers["check_hard"].model
    assert len(calls_for(llm, "fact_check_hard")) >= 1


def test_a_confident_contradiction_is_still_double_checked(cfg):
    state, llm, _ = run(cfg, ("contradicted", 0.99), ("supported", 0.8))
    c = next(c for c in state["claims"] if c["speaker"] == "ceo")
    assert c["escalated"] and c["verdict"] == "supported"                  # false accusations are the costly error


def test_the_vc_sees_the_flagged_claims_and_credibility(cfg):
    state, llm, _ = run(cfg, ("unverifiable", 0.9), rounds=2)
    second_vc = calls_for(llm, "vc")[-1]["user"]
    assert "FACT-CHECK of the last exchange" in second_vc and "unverifiable" in second_vc
    assert "Supported claims so far: CEO 0/2" in second_vc


def test_citations_must_have_been_retrieved(cfg):
    state, _, _ = run(cfg, ("supported", 0.95), rounds=1)
    ceo = next(t for t in state["turns"] if t["speaker"] == "ceo")
    assert ceo["evidence_ids"] == ["c1"] and ceo["invalid_citations"] == 1


def test_every_retrieval_is_era_scoped_including_fact_check_lookups(cfg):
    ret = FakeRetriever([chunk("c1")])
    make_run(cfg, claims_responder(("supported", 0.95)), rounds=1, retriever=ret)
    assert ret.calls and {era for _, era, _, _ in ret.calls} == {"pre_covid"}


def test_credibility_summary():
    claims = [{"speaker": "ceo", "verdict": "supported"}, {"speaker": "ceo", "verdict": "contradicted"},
              {"speaker": "devil", "verdict": "not_checked"}]
    assert credibility(claims) == "CEO 1/2"


def test_fact_check_spend_is_logged_so_run_totals_are_complete(cfg):
    _, _, deps = run(cfg, ("supported", 0.95), rounds=1)
    assert deps.fact_check_costs and all(p["cost_usd"] > 0 for _, p in deps.fact_check_costs)


def test_miscalculated_claim_is_caught_in_code_and_shown_to_vc(cfg):
    def responder(role, system, user, schema, n):
        if role == "ceo" and schema is Statement:
            return Statement(point="10% of $100 million is $20 million.")
        if schema is ClaimExtraction and "10% of" in user:
            return ClaimExtraction(claims=[ExtractedClaim(text="10% of $100 million is $20 million", type="number")],
                                   relies_on_later_knowledge=False, later_knowledge_reason="")
        return default_responder(role, system, user, schema, n)

    state, llm, _ = make_run(cfg, responder, rounds=2, retriever=FakeRetriever())
    claim = next(c for c in state["claims"] if c["speaker"] == "ceo")
    assert claim["verdict"] == "miscalculated" and claim["checker_model"] == "python"
    assert "miscalculated" in calls_for(llm, "vc")[-1]["user"]


def test_unsupported_verdict_without_valid_citation_fails_closed(cfg):
    def responder(role, system, user, schema, n):
        if schema is VerdictBatch:
            return VerdictBatch(verdicts=[ClaimVerdict(ref="C1", verdict="supported", confidence=0.99,
                                                      evidence_ids=["invented"], note="supposedly")])
        return default_responder(role, system, user, schema, n)

    state, llm, _ = make_run(cfg, responder, rounds=1, retriever=FakeRetriever())
    assert all(c["verdict"] == "unverifiable" for c in state["claims"] if c["type"] == "fact")
    assert calls_for(llm, "fact_check_hard")  # low-confidence downgrade gets a second check


def test_no_evidence_is_unverifiable_without_a_verdict_model_call(cfg):
    state, llm, _ = make_run(cfg, rounds=1, retriever=FakeRetriever([]))
    checked = [c for c in state["claims"] if c["type"] == "fact"]
    assert checked and all(c["verdict"] == "unverifiable" and c["checker_model"] == "python" for c in checked)
    assert not calls_for(llm, "fact_check")


def test_vc_factual_assumptions_are_checked_too(cfg):
    def responder(role, system, user, schema, n):
        if role == "vc_opening" and schema is VCQuestion:
            return VCQuestion(point="Boston has 50,427 manholes; how do you reach them?", addressed_to="ceo")
        if schema is ClaimExtraction and "50,427 manholes" in user:
            return ClaimExtraction(claims=[ExtractedClaim(text="Boston has 50,427 manholes.", type="fact")],
                                   relies_on_later_knowledge=False, later_knowledge_reason="")
        if schema is VerdictBatch:
            return VerdictBatch(verdicts=[ClaimVerdict(ref="C1", verdict="supported", confidence=0.95,
                                                      evidence_ids=["c1"], note="case evidence")])
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=1, retriever=FakeRetriever([chunk("c1")]))
    vc_claims = [c for c in state["claims"] if c["speaker"] == "vc"]
    assert len(vc_claims) == 1
    assert vc_claims[0]["verdict"] == "supported" and vc_claims[0]["evidence_ids"] == ["c1"]


def test_the_checker_model_cannot_issue_miscalculated_itself():
    """Only the Python arithmetic check may say "miscalculated"; a model verdict is limited to evidence verdicts."""
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        ClaimVerdict(ref="C1", verdict="miscalculated", confidence=0.9, evidence_ids=[], note="n")


def test_checker_notes_with_hindsight_never_reach_the_vc(cfg):
    leaky = "CDC national wastewater surveillance in 2020 made this moot"

    def responder(role, system, user, schema, n):
        if schema is VerdictBatch:
            refs = [r for r in ("C1", "C2", "C3") if r in user]
            return VerdictBatch(verdicts=[ClaimVerdict(ref=r, verdict="unverifiable", confidence=0.95,
                                                       evidence_ids=[], note=leaky) for r in refs])
        return default_responder(role, system, user, schema, n)

    _, llm, _ = make_run(cfg, responder, rounds=2, retriever=FakeRetriever([chunk("c1")]))
    vc = calls_for(llm, "vc")[-1]["user"]
    assert "unverifiable" in vc and "note withheld" in vc
    assert "CDC" not in vc and "2020" not in vc
