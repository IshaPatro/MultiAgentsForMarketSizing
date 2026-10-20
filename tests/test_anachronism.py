"""Leakage tests for the model's own knowledge (implementation.md section 9, leak 3)."""
from conftest import FakeRetriever, calls_for, make_run

from debate.llm.scripted import default_responder
from debate.state import ClaimExtraction, ExtractedClaim, Statement

HINDSIGHT = "As COVID showed in 2020, wastewater surveillance is critical."


def extraction(flag=False, reason="", claims=None):
    return ClaimExtraction(claims=claims if claims is not None else [ExtractedClaim(text="A claim.", type="fact")],
                           relies_on_later_knowledge=flag, later_knowledge_reason=reason)


def test_forbidden_term_blocks_without_any_model_call_and_turn_is_regenerated(cfg):
    seen = {"n": 0}

    def responder(role, system, user, schema, n):
        if role == "ceo" and schema is Statement:
            seen["n"] += 1
            if seen["n"] == 1:
                return Statement(point=HINDSIGHT)
            assert "rejected" in user and "2017-12-31" in user       # correction note names the cutoff
            return Statement(point="Cities need opioid data now.")
        return default_responder(role, system, user, schema, n)

    state, llm, deps = make_run(cfg, responder, rounds=1)
    ceo = next(t for t in state["turns"] if t["speaker"] == "ceo")
    assert ceo["content"] == "Cities need opioid data now." and not ceo["blocked"] and ceo["regenerations"] == 1
    assert [t for t, _ in deps.events] == ["anachronism_block"]
    # the forbidden-term path is deterministic: only the second, clean attempt reached claim extraction
    assert len([c for c in calls_for(llm, "claim_extract") if "COVID" in c["user"]]) == 0


def test_planted_hindsight_never_reaches_the_vc_or_the_transcript(cfg):
    def responder(role, system, user, schema, n):
        if role == "devil" and schema is Statement:
            return Statement(point=HINDSIGHT)               # always hindsight, even after correction
        return default_responder(role, system, user, schema, n)

    state, llm, deps = make_run(cfg, responder, rounds=3)
    devil = [t for t in state["turns"] if t["speaker"] == "devil"]
    assert all(t["blocked"] and "COVID" not in t["content"] for t in devil)
    assert all(t["content"].startswith("(withheld") for t in devil)
    assert not any("covid" in t["content"].lower() for t in state["turns"])
    for call in calls_for(llm, "vc"):                         # the VC never saw the planted text
        assert "covid" not in call["user"].lower()
    kinds = [t for t, _ in deps.events]
    assert kinds.count("turn_withheld") == 3 and kinds.count("anachronism_block") == 9   # 1 try + 2 regenerations each
    assert deps.recorded_claims == [c for c in deps.recorded_claims if c["speaker"] != "devil"]  # blocked turns are not fact-checked


def test_model_flag_blocks_subtle_hindsight_the_keyword_list_misses(cfg):
    def responder(role, system, user, schema, n):
        if schema is ClaimExtraction and "retroactively obvious" in user:
            return extraction(flag=True, reason="presupposes later events")
        if role == "cfo" and schema is Statement and "rejected" not in user:
            return Statement(point="Looking back, the retroactively obvious need was clear.")
        return default_responder(role, system, user, schema, n)

    from debate.state import VCQuestion
    def vc_to_cfo(role, system, user, schema, n):
        if schema is VCQuestion and role == "vc":
            return VCQuestion(point="Pricing?", addressed_to="cfo")
        return responder(role, system, user, schema, n)

    state, _, deps = make_run(cfg, vc_to_cfo, rounds=2)
    cfo = next(t for t in state["turns"] if t["speaker"] == "cfo")
    assert cfo["regenerations"] == 1 and not cfo["blocked"]
    assert deps.events[0][1]["reason"] == "presupposes later events"


def test_future_years_are_passed_to_the_checker_as_a_hint_not_blocked(cfg):
    def responder(role, system, user, schema, n):
        if role == "ceo" and schema is Statement:
            return Statement(point="We plan to cover 100 cities by 2020.")
        return default_responder(role, system, user, schema, n)

    state, llm, deps = make_run(cfg, responder, rounds=1)
    ceo = next(t for t in state["turns"] if t["speaker"] == "ceo")
    assert not ceo["blocked"] and deps.events == []
    assert "[2020]" in next(c for c in calls_for(llm, "claim_extract") if "by 2020" in c["user"])["user"]


def test_hypothetical_pandemic_is_not_deterministically_blocked(cfg):
    from debate.anachronism import forbidden_hits
    from debate.config import load_era

    assert forbidden_hits("We could prepare for a future pandemic.", load_era("pre_covid")) == []


def test_claims_are_extracted_once_per_turn_and_stored_on_it(cfg):
    state, _, _ = make_run(cfg, rounds=1, retriever=FakeRetriever())
    assert all(len(t["claims"]) == 1 for t in state["turns"] if t["speaker"] in ("ceo", "devil"))


def test_the_correction_prompt_never_repeats_the_hindsight_it_rejected(cfg):
    """Regression: the checker's own reason ("after the COVID-19 pandemic") must not be fed back to the agent."""
    corrections = []

    def responder(role, system, user, schema, n):
        if schema is ClaimExtraction and "hindsight bait" in user:
            return extraction(flag=True, reason="only widespread after the COVID-19 pandemic and the 2020 CDC network")
        if role == "ceo" and schema is Statement:
            if "rejected" in user:
                corrections.append(user)
                return Statement(point="Clean answer.")
            return Statement(point="A hindsight bait statement.")
        return default_responder(role, system, user, schema, n)

    make_run(cfg, responder, rounds=1)
    assert corrections
    for text in corrections:
        low = text.lower()
        assert "covid" not in low and "pandemic" not in low and "cdc" not in low


def test_vc_hindsight_is_blocked_before_an_executive_or_later_vc_sees_it(cfg):
    from debate.state import VCQuestion

    def responder(role, system, user, schema, n):
        if role in ("vc_opening", "vc") and schema is VCQuestion:
            return VCQuestion(point=HINDSIGHT, addressed_to="ceo")
        return default_responder(role, system, user, schema, n)

    state, llm, deps = make_run(cfg, responder, rounds=2)
    vc_turns = [t for t in state["turns"] if t["speaker"] == "vc"]
    assert len(vc_turns) == 2 and all(t["blocked"] for t in vc_turns)
    assert all("covid" not in t["content"].lower() for t in state["turns"])
    assert all("covid" not in c["user"].lower() for c in calls_for(llm, "ceo"))
    assert sum(t == "turn_withheld" for t, _ in deps.events) == 2
