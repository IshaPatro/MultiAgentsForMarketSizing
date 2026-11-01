from debate.eval import build_dashboard
from debate.rag.retriever import Chunk, Retrieval
from debate.state import Turn, Usage
from debate.storage.sqlite_store import SqliteStore


def _run(store, run_id="r1"):
    store.start_run(run_id=run_id, company_id="biobot", thread_id=run_id,
                    config={"dry_run": False, "profile": "routed", "prompt_version": "v1",
                            "limits": {"max_rounds": 1, "max_words_per_turn": 60}})
    store.add_turn(run_id, Turn(seq=1, round=1, speaker="ceo", addressed_to="vc", content="A short claim.",
                                model="m", tier="system2", usage=Usage()))
    store.add_retrieval(run_id, 1, "factcheck", Retrieval("claim", [Chunk("c1", "Evidence", 1, "", "case")]))
    store.add_claims(run_id, [{"claim_id": f"{run_id}-x", "turn_seq": 1, "speaker": "ceo", "text": "claim",
                               "type": "fact", "verdict": "supported", "confidence": 1, "evidence_ids": ["c1"],
                               "note": "", "escalated": False, "checker_model": "m"}])
    store.add_decision(run_id, {"decision": "pass"})
    store.end_run(run_id, "completed", 0.01)


def test_sql_dashboard_checks_claim_citations_against_retrievals():
    store = SqliteStore(":memory:")
    _run(store)
    report = build_dashboard(store, version="v1")
    assert "100.0% (1/1)" in report
    assert "Over-limit turns" not in report      # answers are not length-limited any more
    # A citation that was never retrieved for that turn no longer counts as grounded.
    store.conn.execute("UPDATE claims SET evidence_ids='[\"not-retrieved\"]'")
    assert "0.0% (0/1)" in build_dashboard(store, version="v1")


def test_miscalculations_fail_the_maths_target():
    store = SqliteStore(":memory:")
    _run(store)
    store.add_claims("r1", [{"claim_id": "r1-t1-calc", "turn_seq": 1, "speaker": "cfo", "text": "2 x 3 = 7",
                             "type": "number", "verdict": "miscalculated", "confidence": 1, "evidence_ids": [],
                             "note": "", "escalated": False, "checker_model": "python"}])
    assert "Calculations that do not add up | 1 | 0 | FAIL" in build_dashboard(store, version="v1")


def test_decision_stability_needs_five_comparable_runs():
    store = SqliteStore(":memory:")
    for i in range(4):
        _run(store, f"r{i}")
    assert "Decision stability | <5 comparable runs | ≥4/5 | PENDING" in build_dashboard(store, version="v1")
    _run(store, "r4")
    assert "Decision stability | 5/5 (routed, 1 rounds) | ≥4/5 | PASS" in build_dashboard(store, version="v1")


def test_stability_command_repeats_one_debate_without_models(monkeypatch):
    import argparse

    import debate.cli as cli

    calls = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr(cli, "eval_report_cmd", lambda args: 0)
    args = argparse.Namespace(company="biobot", repeats=2, rounds=3, profile="routed")
    assert cli.eval_stability_cmd(args) == 0
    assert len(calls) == 2 and all("--era" not in c and c[c.index("--rounds") + 1] == "3" for c in calls)
