from conftest import needs_pg, FakeRetriever, chunk, make_run

from debate.nodes.agents import BudgetExceeded
from debate.state import VCQuestion
from debate.llm.scripted import default_responder
import pytest


def test_three_rounds_give_nine_turns_in_order(cfg):
    state, _, _ = make_run(cfg, rounds=3)
    assert "era" not in state["decision"]
    assert set(state["decision"]["score_drivers"]) == {
        "team", "market", "product", "traction", "moat", "timing", "business_model"}
    speakers = [t["speaker"] for t in state["turns"]]
    # 9 debate turns + a moderator notice per VC question (2) + a VC-assistant verdict after every attack (3)
    assert len(speakers) == 14
    assert speakers[0] == "vc" and speakers[1] == "ceo"          # opening asks the CEO for the pitch
    debate = [s for s in speakers if s not in ("moderator", "assistant")]
    assert debate[2::3] == ["devil"] * 3                          # devil attacks after every answer
    assert speakers[:4] == ["vc", "ceo", "devil", "assistant"]    # the assistant checks each exchange
    assert speakers[4:7] == ["vc", "moderator", speakers[6]] and speakers[7:9] == ["devil", "assistant"]
    assert [t["seq"] for t in state["turns"]] == list(range(1, 15))
    assert [t["round"] for t in state["turns"]] == [1] * 4 + [2] * 5 + [3] * 5
    notice = state["turns"][5]
    assert notice["content"] == "This question will be taken up by the CFO." and notice["addressed_to"] == "cfo"
    assert state["turns"][6]["speaker"] == "cfo"                  # the answer comes from whoever the moderator named
    verdicts = [t for t in state["turns"] if t["speaker"] == "assistant"]
    assert all(v["content"] in ("Facts are correct.", "Facts are incorrect.") and v["addressed_to"] == "vc" for v in verdicts)


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


def test_vc_can_cite_retrieved_premises(cfg):
    def responder(role, system, user, schema, n):
        if schema is VCQuestion:
            return VCQuestion(point="Boston has 50,427 manholes; how will you sample them?",
                              addressed_to="ceo", evidence_ids=["case_p11_c1"])
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=1, retriever=FakeRetriever([chunk("case_p11_c1")]))
    vc = state["turns"][0]
    assert vc["evidence_ids"] == ["case_p11_c1"] and vc["invalid_citations"] == 0


def _drop_threads(data_dir):
    """CLI runs checkpoint to the real Postgres; remove this test's threads so runs do not pile up."""
    import sqlite3

    from debate.memory.setup import persistence

    threads = [r[0] for r in sqlite3.connect(data_dir / "debate.sqlite").execute("SELECT thread_id FROM runs")]
    with persistence() as (saver, _):
        for thread_id in threads:
            saver.delete_thread(thread_id)


@needs_pg
def test_long_runs_do_not_hit_the_graph_recursion_limit(tmp_path, monkeypatch, capsys):
    import debate.cli as cli

    monkeypatch.setattr(cli, "DATA", tmp_path)
    try:
        assert cli.main(["run", "--dry-run", "--rounds", "16"]) == 0
        assert "completed: 79 turns" in capsys.readouterr().out
    finally:
        _drop_threads(tmp_path)


@needs_pg
def test_a_crashing_run_is_recorded_as_error_not_left_running(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    import debate.cli as cli

    monkeypatch.setattr(cli, "DATA", tmp_path)

    class PlantedError(RuntimeError):
        pass

    class Exploding:
        def invoke(self, *a, **k):
            raise PlantedError("planted")

    monkeypatch.setattr(cli, "build_graph", lambda deps, saver, **kwargs: Exploding())
    with pytest.raises(PlantedError):
        cli.main(["run", "--dry-run", "--rounds", "1"])
    conn = sqlite3.connect(cli.DATA / "debate.sqlite")
    run_id, status = conn.execute("SELECT run_id, status FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    assert status == "error"
    assert conn.execute("SELECT type FROM events WHERE run_id=?", (run_id,)).fetchone() == ("run_failed",)
