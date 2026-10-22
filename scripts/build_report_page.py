"""Embed saved SQLite debates in standalone overview and conversation pages.

Run from any directory with: python3 scripts/build_report_page.py
The generated HTML opens directly from disk without fetch requests.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "debate.sqlite"
PAGES = (ROOT / "index.html", ROOT / "conversation.html")
DATA_TAG = re.compile(r'(<script id="report-data" type="application/json">).*?(</script>)', re.S)


def _json(value: str | None, default):
    return json.loads(value) if value else default


def build_data(db: Path = DB, reports: Path | None = None,
               include_dry_runs: bool = False) -> dict:
    if not db.is_file():
        raise FileNotFoundError(f"No saved debate database at {db}. Run a debate before rebuilding the pages.")
    reports = reports or ROOT / "reports" / "runs"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        runs = []
        companies = set()
        for row in conn.execute(
            "SELECT r.*, d.decision_json FROM runs r "
            "LEFT JOIN decisions d ON d.run_id=r.run_id "
            "WHERE r.status='completed' AND EXISTS (SELECT 1 FROM turns t WHERE t.run_id=r.run_id) "
            "ORDER BY r.started_at DESC, r.run_id DESC"
        ):
            run_id = row["run_id"]
            config = _json(row["config_json"], {})
            if (config.get("dry_run") and not include_dry_runs) or row["company_id"] in companies:
                continue
            companies.add(row["company_id"])
            claims_by_turn: dict[int, list[dict]] = {}
            for claim in conn.execute(
                "SELECT turn_seq, text, verdict, note, evidence_ids FROM claims "
                "WHERE run_id=? ORDER BY turn_seq, claim_id", (run_id,)
            ):
                claims_by_turn.setdefault(claim["turn_seq"], []).append({
                    "text": claim["text"], "verdict": claim["verdict"], "note": claim["note"],
                    "evidence_ids": _json(claim["evidence_ids"], []),
                })
            turns = []
            for turn in conn.execute("SELECT * FROM turns WHERE run_id=? ORDER BY seq", (run_id,)):
                turns.append({
                    "seq": turn["seq"], "round": turn["round"], "speaker": turn["speaker"].upper(),
                    "addressed_to": (turn["addressed_to"] or "room").upper(),
                    "content": turn["content"], "evidence_ids": _json(turn["evidence_ids"], []),
                    "claims": claims_by_turn.get(turn["seq"], []), "word_count": turn["word_count"],
                    "invalid_citations": turn["invalid_citations"], "topic": turn["topic"],
                    "follow_up": bool(turn["is_follow_up"]), "calculation": turn["calculation"],
                    "calculation_ok": turn["calculation_ok"],
                    "assumptions": _json(turn["assumptions_json"], []),
                    "estimates": _json(turn["estimates_json"], []),
                })
            runs.append({
                "id": run_id, "company": row["company_id"], "started": row["started_at"],
                "status": row["status"], "dry_run": bool(config.get("dry_run")),
                "cost": row["total_cost_usd"], "decision": _json(row["decision_json"], {}),
                "report_available": (reports / f"{run_id}.md").is_file(), "turns": turns,
            })
    return {"runs": runs}


def write_pages(data: dict, pages: tuple[Path, ...] = PAGES) -> None:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")
    for page in pages:
        replaced, count = DATA_TAG.subn(lambda match: match.group(1) + payload + match.group(2), page.read_text())
        if count != 1:
            raise RuntimeError(f"{page} needs exactly one report-data script tag")
        page.write_text(replaced)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-dry-runs", action="store_true", help="also show scripted placeholder runs")
    args = parser.parse_args()
    data = build_data(include_dry_runs=args.include_dry_runs)
    write_pages(data)
    print(f"Embedded {len(data['runs'])} saved runs in {', '.join(str(p) for p in PAGES)}")


if __name__ == "__main__":
    main()
