"""The offline pages must retain every saved turn and its support fields."""
import importlib.util
import json
import sqlite3
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_report_page.py"
spec = importlib.util.spec_from_file_location("build_report_page", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_static_snapshot_contains_complete_conversation_without_markdown_export(tmp_path):
    db = tmp_path / "debate.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE runs (run_id TEXT, company_id TEXT, started_at TEXT, status TEXT,
                config_json TEXT, total_cost_usd REAL);
            CREATE TABLE decisions (run_id TEXT, decision_json TEXT);
            CREATE TABLE turns (run_id TEXT, seq INTEGER, round INTEGER, speaker TEXT,
                addressed_to TEXT, content TEXT, evidence_ids TEXT, word_count INTEGER,
                invalid_citations INTEGER, topic TEXT, is_follow_up INTEGER, calculation TEXT,
                calculation_ok INTEGER, assumptions_json TEXT, estimates_json TEXT);
            CREATE TABLE claims (run_id TEXT, turn_seq INTEGER, claim_id TEXT, text TEXT,
                verdict TEXT, note TEXT, evidence_ids TEXT);
        """)
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                     ("run-1", "biobot", "2026-10-22", "completed", '{"dry_run":false}', 0.2))
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                     ("dry-1", "biobot", "2026-10-23", "completed", '{"dry_run":true}', 0.0))
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                     ("older-1", "biobot", "2026-10-21", "completed", '{"dry_run":false}', 0.1))
        conn.execute("INSERT INTO decisions VALUES (?,?)",
                     ("run-1", '{"decision":"pass","conviction":0.8}'))
        conn.executemany("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("run-1", 2, 1, "cfo", "vc", "Answer <detail>", '["source-2"]', 2, 0,
             "market_size", 1, "10 * 2 = 20", 1, '["Price assumption"]',
             '[{"method":"Bottom up","calculation":"10 * 2 = 20","assumptions":[],"calculation_ok":true}]'),
            ("run-1", 1, 1, "vc", "cfo", "Question", "[]", 1, 0,
             "market_size", 0, "", None, "[]", "[]"),
        ])
        conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?)",
                     ("run-1", 2, "c1", "20 customers", "supported", "Checked", "[\"source-2\"]"))
        conn.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("dry-1", 1, 1, "vc", "ceo", "Placeholder", "[]", 1, 0, "", 0, "", None, "[]", "[]"))
        conn.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("older-1", 1, 1, "vc", "ceo", "Older conversation", "[]", 2, 0, "", 0, "", None, "[]", "[]"))
    data = builder.build_data(db, tmp_path / "missing-reports")
    assert len(data["runs"]) == 1
    assert data["runs"][0]["id"] == "run-1"
    assert [r["id"] for r in builder.build_data(db, include_dry_runs=True)["runs"]] == ["dry-1"]
    run = data["runs"][0]
    assert [t["seq"] for t in run["turns"]] == [1, 2]
    assert run["report_available"] is False
    assert run["turns"][1]["speaker"] == "CFO"
    assert run["turns"][1]["follow_up"] is True
    assert run["turns"][1]["estimates"][0]["method"] == "Bottom up"
    assert run["turns"][1]["claims"][0]["verdict"] == "supported"

    pages = (tmp_path / "index.html", tmp_path / "conversation.html")
    for page in pages:
        page.write_text('<script id="report-data" type="application/json">{"runs":[]}</script>')
    builder.write_pages(data, pages)
    for page in pages:
        html = page.read_text()
        assert "Answer <detail>" not in html
        payload = html.split('type="application/json">')[1].split("</script>")[0]
        assert json.loads(payload)["runs"][0]["turns"][1]["content"] == "Answer <detail>"
