from conftest import FakeRetriever, chunk, make_run

from debate.nodes.agents import BudgetExceeded
from debate.state import VCQuestion
from debate.llm.scripted import default_responder
import pytest


def test_three_rounds_give_nine_turns_in_order(cfg):
    state, _, _ = make_run(cfg, rounds=3)
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

    state, _, _ = make_run(cfg, responder, rounds=3)
    answers = [t["speaker"] for t in state["turns"] if t["speaker"] in ("ceo", "cfo", "cto")]
    assert answers == ["ceo", "cfo", "cfo"]
    devil_targets = [t["addressed_to"] for t in state["turns"] if t["speaker"] == "devil"]
    assert devil_targets == ["ceo", "cfo", "cfo"]


def test_each_agent_sees_the_simulated_date_and_only_its_era_brief(cfg):
    _, llm, _ = make_run(cfg, rounds=1)
    for call in llm.calls:
        if call["role"] == "date_check":
            continue
        assert "2017-12-31" in call["system"]
        assert "COVID" not in call["system"].upper().replace("PRE-COVID", "")


def test_context_window_is_bounded(cfg):
    cfg.limits.context_turns = 2
    _, llm, _ = make_run(cfg, rounds=3)
    last_vc_prompt = [c for c in llm.calls if c["role"] == "vc"][-1]["user"]
    assert last_vc_prompt.split("Meeting so far:")[1].split("FACT-CHECK")[0].count("->") == 2


def test_budget_cap_stops_the_run(cfg):
    cfg.limits.max_cost_usd_per_run = 0.003
    with pytest.raises(BudgetExceeded):
        make_run(cfg, rounds=3)


def test_offline_graph_retrieves_and_records_real_citation_ids(cfg):
    state, llm, _ = make_run(cfg, rounds=1, retriever=FakeRetriever([chunk("case_p11_c1")]))
    arguments = [t for t in state["turns"] if t["speaker"] in ("ceo", "devil")]
    assert [t["evidence_ids"] for t in arguments] == [["case_p11_c1"], ["case_p11_c1"]]
    assert all(t["invalid_citations"] == 0 for t in arguments)
    assert any("[case_p11_c1]" in c["user"] for c in llm.calls if c["role"] == "ceo")


def test_long_runs_do_not_hit_the_graph_recursion_limit(capsys):
    from debate.cli import main

    assert main(["run", "--dry-run", "--rounds", "16"]) == 0
    assert "completed: 48 turns" in capsys.readouterr().out


def test_a_crashing_run_is_recorded_as_error_not_left_running(monkeypatch):
    import sqlite3

    import pytest

    import debate.cli as cli
    from debate.rag.retriever import LeakError

    class Exploding:
        def invoke(self, *a, **k):
            raise LeakError("planted")

    monkeypatch.setattr(cli, "build_graph", lambda deps, saver: Exploding())
    with pytest.raises(LeakError):
        cli.main(["run", "--dry-run", "--rounds", "1"])
    conn = sqlite3.connect(cli.DATA / "debate.sqlite")
    run_id, status = conn.execute("SELECT run_id, status FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    assert status == "error"
    assert conn.execute("SELECT type FROM events WHERE run_id=?", (run_id,)).fetchone() == ("run_failed",)
