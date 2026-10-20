from conftest import make_run

from debate.nodes.agents import BudgetExceeded
from debate.state import VCQuestion
from debate.llm.scripted import default_responder
import pytest


def test_three_rounds_give_nine_turns_in_order(cfg):
    state, _, _, _ = make_run(cfg, rounds=3)
    speakers = [t["speaker"] for t in state["turns"]]
    assert len(speakers) == 9
    assert speakers[0] == "vc" and speakers[1] == "ceo"          # opening asks the CEO for the pitch
    assert speakers[2::3] == ["devil"] * 3                        # devil attacks after every answer
    assert [t["seq"] for t in state["turns"]] == list(range(1, 10))
    assert [t["round"] for t in state["turns"]] == [1, 1, 1, 2, 2, 2, 3, 3, 3]


def test_vc_question_is_routed_to_the_executive_it_names(cfg):
    def responder(role, system, user, schema, n):
        if schema is VCQuestion and role == "vc":
            return VCQuestion(point="What is your pricing?", addressed_to="cfo")
        return default_responder(role, system, user, schema, n)

    state, _, _, _ = make_run(cfg, responder, rounds=3)
    answers = [t["speaker"] for t in state["turns"] if t["speaker"] in ("ceo", "cfo", "cto")]
    assert answers == ["ceo", "cfo", "cfo"]
    devil_targets = [t["addressed_to"] for t in state["turns"] if t["speaker"] == "devil"]
    assert devil_targets == ["ceo", "cfo", "cfo"]


def test_each_agent_sees_the_simulated_date_and_only_its_era_brief(cfg):
    _, llm, _, _ = make_run(cfg, rounds=1)
    for call in llm.calls:
        assert "2017-12-31" in call["system"]
        assert "COVID" not in call["system"].upper().replace("PRE-COVID", "")


def test_context_window_is_bounded(cfg):
    cfg.limits.context_turns = 2
    _, llm, _, _ = make_run(cfg, rounds=3)
    last_user = llm.calls[-1]["user"]
    assert last_user.count("->") == 2


def test_budget_cap_stops_the_run(cfg):
    cfg.limits.max_cost_usd_per_run = 0.003
    with pytest.raises(BudgetExceeded):
        make_run(cfg, rounds=3)
