from debate.cli import compare_cmd
from debate.state import Usage
from debate.storage.sqlite_store import SqliteStore


def _run(store, run_id, profile, cost):
    store.start_run(run_id=run_id, company_id="biobot", thread_id=run_id, config={"profile": profile, "dry_run": True,
                                               "limits": {"max_rounds": 1}})
    model = "claude-opus-5-5" if profile == "opus_low" else "claude-haiku-5-5"
    store.add_model_call(run_id, "claim_extract", "fast", model,
                         Usage(input_tokens=100, output_tokens=20, cost_usd=cost))
    store.end_run(run_id, "completed", cost)


def test_model_call_stats_and_profile_comparison(tmp_path, monkeypatch, capsys):
    store = SqliteStore(tmp_path / "debate.sqlite")
    _run(store, "routed-run", "routed", 0.001)
    _run(store, "opus-run", "opus_low", 0.004)
    assert store.call_stats("routed-run")["system1_calls"] == 1
    assert store.call_stats("opus-run")["system1_calls"] == 0
    monkeypatch.setattr("debate.cli.DATA", tmp_path)
    args = type("Args", (), {"routed": "routed-run", "baseline": "opus-run"})()
    assert compare_cmd(args) == 0
    output = capsys.readouterr().out
    assert "simulated estimates" in output and "75.0%" in output


def test_compare_refuses_runs_made_differently(tmp_path, monkeypatch, capsys):
    """Regression: a simulated dry-run must not be compared with billed usage (it reported 99.7% savings)."""
    store = SqliteStore(tmp_path / "debate.sqlite")
    _run(store, "routed-run", "routed", 0.001)
    store.start_run(run_id="opus-real", company_id="biobot", thread_id="opus-real", config={"profile": "opus_low", "dry_run": False, "limits": {"max_rounds": 1}})
    store.end_run("opus-real", "completed", 0.30)
    monkeypatch.setattr("debate.cli.DATA", tmp_path)
    args = type("Args", (), {"routed": "routed-run", "baseline": "opus-real"})()
    assert compare_cmd(args) == 2
    assert "dry_run" in capsys.readouterr().err
