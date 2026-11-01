import json

from conftest import needs_pg

from debate.export import export_runs, run_markdown
from debate.state import Turn, Usage
from debate.storage.sqlite_store import SqliteStore


def _store(tmp_path, dry_run=False):
    store = SqliteStore(tmp_path / "debate.sqlite")
    store.start_run(run_id="r1", company_id="biobot", thread_id="t",
                    config={"dry_run": dry_run, "profile": "routed", "prompt_version": "abc",
                            "limits": {"max_rounds": 1}, "models": {"tiers": {"debate": {"model": "claude-sonnet-5-5"}}}})
    store.add_turn("r1", Turn(seq=1, round=1, speaker="cfo", addressed_to="vc", content="Budgets are $88B | big.",
                              model="claude-sonnet-5-5", tier="system2", usage=Usage(input_tokens=10, output_tokens=5),
                              evidence_ids=["biobot_case_p11_c3"], calculation="4 x $110B x 20% = $22B",
                              assumptions=["cities spend 20% on public health"], calculation_ok=False))
    store.add_claims("r1", [{"claim_id": "r1-t1-calc", "turn_seq": 1, "speaker": "cfo", "text": "4 x $110B x 20% = $22B",
                             "type": "number", "verdict": "miscalculated", "confidence": 1.0, "evidence_ids": [],
                             "note": "Shown calculation does not add up", "escalated": False, "checker_model": "python"}])
    store.add_event("r1", "leak_attempt", {"agent": "cfo", "namespace": ["vc", "portfolio_theses"], "write": False})
    store.add_model_call("r1", "cfo", "debate", "claude-sonnet-5-5", Usage(input_tokens=10, output_tokens=5, cost_usd=0.01))
    store.add_decision("r1", {"decision": "pass", "conviction": 0.7, "scores": {"team": 3},
                              "score_drivers": {"team": {"reason": "strong founders", "claim_ids": []}},
                              "key_reasons_for": ["a"], "key_reasons_against": ["b"], "deal_terms_sketch": "none",
                              "what_would_change_my_mind": ["c"], "cited_claim_ids": []})
    store.end_run("r1", "completed", 0.01)
    return store


def test_run_page_has_front_matter_maths_assumptions_checks_decision_and_costs(tmp_path):
    md = run_markdown(_store(tmp_path), "r1")
    front = md.split("---")[1]
    assert '"run_id": ' not in front and 'run_id: "r1"' in front and 'decision: "pass"' in front
    assert "**Maths** (❌ does not add up): `4 x $110B x 20% = $22B`" in md
    assert "- cities spend 20% on public health" in md
    assert "HBS case 821-045" in md and "p.11" in md               # citation shows the source title, not text
    assert "miscalculated" in md and "| Team | 3/5 | strong founders |" in md
    assert "Budgets are $88B | big." in md                         # transcript text kept verbatim
    assert "$0.01000" in md and "Cross-agent memory access attempts: 1" in md
    assert "era" not in md.split("---")[1]                          # no era / cutoff front matter


def test_export_writes_pages_and_an_index_but_skips_dry_runs_by_default(tmp_path):
    store = _store(tmp_path, dry_run=True)
    out = tmp_path / "site"
    assert export_runs(store, out) == [out / "index.md"]
    written = export_runs(store, out, include_dry_runs=True)
    assert out / "runs" / "r1.md" in written
    assert "[Biobot Analytics](runs/r1.md) (dry run)" in (out / "index.md").read_text()


@needs_pg
def test_cli_dry_run_then_export(tmp_path, monkeypatch):
    import sqlite3

    import debate.cli as cli
    from debate.memory.setup import persistence

    monkeypatch.setattr(cli, "DATA", tmp_path)
    try:
        assert cli.main(["run", "--dry-run", "--rounds", "1"]) == 0
        assert cli.main(["export", "--out", str(tmp_path / "site"), "--include-dry-runs"]) == 0
        pages = list((tmp_path / "site" / "runs").glob("*.md"))
        assert len(pages) == 1 and "## VC decision" in pages[0].read_text()
    finally:
        threads = [r[0] for r in sqlite3.connect(tmp_path / "debate.sqlite").execute("SELECT thread_id FROM runs")]
        with persistence() as (saver, _):
            for thread_id in threads:
                saver.delete_thread(thread_id)
