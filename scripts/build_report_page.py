"""Embed both saved debate profiles in standalone conversation and memo pages.

Run from any directory with: python3 scripts/build_report_page.py
The generated HTML opens directly from disk without fetch requests.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "debate.sqlite"
PAGES = (ROOT / "web" / "biobot.html", ROOT / "web" / "decision-memo.html")
PUBLIC_DEMO = ROOT / "site" / "public_demo.json"
DATA_TAG = re.compile(r'(<script id="report-data" type="application/json">).*?(</script>)', re.S)


def _json(value: str | None, default):
    return json.loads(value) if value else default


def _run_metrics(conn: sqlite3.Connection, run_id: str, row: sqlite3.Row) -> dict:
    """Summarize recorded inference usage, including older databases without model_calls."""
    metrics = {"input_tokens": 0, "output_tokens": 0, "latency_s": 0, "wall_s": None, "calls": 0}
    try:
        usage = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
            "COALESCE(SUM(latency_ms), 0) FROM model_calls WHERE run_id=?", (run_id,)
        ).fetchone()
        metrics.update(calls=usage[0], input_tokens=usage[1], output_tokens=usage[2],
                       latency_s=round(usage[3] / 1000))
    except sqlite3.OperationalError:
        pass
    if "ended_at" in row.keys() and row["ended_at"] and row["started_at"]:
        metrics["wall_s"] = round((datetime.fromisoformat(row["ended_at"])
                                   - datetime.fromisoformat(row["started_at"])).total_seconds())
    return metrics


def build_data(db: Path = DB, reports: Path | None = None,
               include_dry_runs: bool = False) -> dict:
    if not db.is_file():
        raise FileNotFoundError(f"No saved debate database at {db}. Run a debate before rebuilding the pages.")
    reports = reports or ROOT / "reports" / "runs"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        runs = []
        profiles = set()
        for row in conn.execute(
            "SELECT r.*, d.decision_json FROM runs r "
            "LEFT JOIN decisions d ON d.run_id=r.run_id "
            "WHERE r.status='completed' AND EXISTS (SELECT 1 FROM turns t WHERE t.run_id=r.run_id) "
            "ORDER BY r.started_at DESC, r.run_id DESC"
        ):
            run_id = row["run_id"]
            config = _json(row["config_json"], {})
            profile = config.get("profile") or "routed"
            key = (row["company_id"], profile)
            if (config.get("dry_run") and not include_dry_runs) or key in profiles:
                continue
            profiles.add(key)
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
                "profile": profile, "metrics": _run_metrics(conn, run_id, row),
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
    parser.add_argument("--public-demo", action="store_true", help="embed the fictional, publishable demo instead of private case transcripts")
    args = parser.parse_args()
    data = json.loads(PUBLIC_DEMO.read_text()) if args.public_demo else build_data(include_dry_runs=args.include_dry_runs)
    write_pages(data)
    print(f"Embedded {len(data['runs'])} saved runs in {', '.join(str(p) for p in PAGES)}")


if __name__ == "__main__":
    main()
