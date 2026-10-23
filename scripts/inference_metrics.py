"""Inference metrics of stored debates, from data/debate.sqlite. Every number is read from what was recorded per API
call (`model_calls`) and per turn; nothing is estimated.

    python3 scripts/inference_metrics.py [RUN_ID ...] [--out reports/inference_comparison.md]
                                         [--versus-out reports/optimized_vs_opus_only.md]

Writes
  - inference_comparison.md   every stored run side by side, plus a per-tier breakdown of each live run
  - optimized_vs_opus_only.md the latest live optimized (routed) run against the latest live all-Opus run
  - reports/raw/<run_id>.json everything stored for each live run

How tokens are counted (Anthropic `usage` semantics):
  - Claude `input_tokens` are the uncached input only; cache writes and cache reads are reported separately and
    priced separately (write 1.25x input, read 0.1x input). `output_tokens` include thinking tokens, billed as output.
  - JEV reports input and output tokens; it bills input only ($0.042 per million), so its output tokens are shown but
    never priced or mixed into billed Claude output.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEBATERS = ("vc", "ceo", "cfo", "cto", "devil")
ANSWERERS = ("ceo", "cfo", "cto", "devil")
SYSTEM1_TIERS = ("fast", "verdict", "route")


def _provider(model: str) -> str:
    return "jev" if model.startswith("jev:") else "claude"


def load(con: sqlite3.Connection, run_id: str) -> dict:
    run = con.execute("SELECT status, config_json, total_cost_usd, started_at, ended_at, company_id FROM runs "
                      "WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        sys.exit(f"unknown run {run_id}")
    calls = [dict(zip(("tier", "model", "rows", "requests", "input", "output", "cache_read", "cache_write", "ms", "usd"), r))
             for r in con.execute(
                 "SELECT tier, model, COUNT(*), SUM(requests), SUM(input_tokens), SUM(output_tokens), "
                 "SUM(cache_read_tokens), SUM(cache_write_tokens), SUM(latency_ms), SUM(cost_usd) "
                 "FROM model_calls WHERE run_id=? GROUP BY tier, model", (run_id,))]
    p = {prov: {k: sum(c[k] or 0 for c in calls if _provider(c["model"]) == prov)
                for k in ("requests", "input", "output", "cache_read", "cache_write", "ms", "usd")}
         for prov in ("claude", "jev")}
    words = con.execute(f"SELECT AVG(word_count), MAX(word_count) FROM turns WHERE run_id=? AND speaker IN "
                        f"({','.join('?' * len(ANSWERERS))})", (run_id, *ANSWERERS)).fetchone()
    speakers = dict(con.execute("SELECT speaker, COUNT(*) FROM turns WHERE run_id=? GROUP BY speaker", (run_id,)).fetchall())
    claims = dict(con.execute("SELECT verdict, COUNT(*) FROM claims WHERE run_id=? GROUP BY verdict", (run_id,)).fetchall())
    escalated = con.execute("SELECT COALESCE(SUM(escalated), 0) FROM claims WHERE run_id=?", (run_id,)).fetchone()[0]
    in_code = con.execute("SELECT COUNT(*) FROM claims WHERE run_id=? AND checker_model='python'", (run_id,)).fetchone()[0]
    decision = con.execute("SELECT decision_json FROM decisions WHERE run_id=?", (run_id,)).fetchone()
    picks = dict(con.execute("SELECT addressed_to, COUNT(*) FROM turns WHERE run_id=? AND speaker='moderator' "
                             "AND is_follow_up=0 GROUP BY addressed_to", (run_id,)).fetchall())
    wall = None
    if run[3] and run[4]:
        wall = (datetime.fromisoformat(run[4]) - datetime.fromisoformat(run[3])).total_seconds()
    usd = p["claude"]["usd"] + p["jev"]["usd"]
    return {
        "id": run_id, "company": run[5], "status": run[0], "cfg": json.loads(run[1]), "recorded_usd": run[2] or 0.0, "usd": usd,
        "calls": calls, "p": p, "s1_requests": sum(c["requests"] or 0 for c in calls if c["tier"] in SYSTEM1_TIERS
                                                    and c["model"] != "claude-opus-5-5"),
        "requests": p["claude"]["requests"] + p["jev"]["requests"], "ms": p["claude"]["ms"] + p["jev"]["ms"],
        "tokens": sum(p["claude"][k] for k in ("input", "output", "cache_read", "cache_write")) + p["jev"]["input"] + p["jev"]["output"],
        "words": words, "speakers": speakers, "vc_turns": speakers.get("vc", 0),
        "debate_turns": sum(speakers.get(s, 0) for s in DEBATERS), "claims": claims, "escalated": escalated,
        "in_code": in_code, "decision": json.loads(decision[0]).get("decision") if decision else "-",
        "picks": picks, "wall": wall,
    }


def reconciles(r: dict) -> str:
    """The run's recorded total (from the per-run budget tracker) must equal the sum of its recorded calls."""
    diff = r["recorded_usd"] - r["usd"]
    return "matches the sum of all recorded calls" if abs(diff) < 1e-6 else f"MISMATCH: run total differs by ${diff:+.6f}"


def dump_raw(con: sqlite3.Connection, run_id: str, out_dir: Path) -> Path:
    """Everything stored for one run (run row, turns, claims, model calls, retrievals, events, decision) as JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con.row_factory = sqlite3.Row
    try:
        data = {"run": dict(con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())}
        for table_name in ("turns", "claims", "model_calls", "retrievals", "events", "decisions"):
            try:
                data[table_name] = [dict(r) for r in con.execute(f"SELECT * FROM {table_name} WHERE run_id=?", (run_id,))]
            except sqlite3.OperationalError:
                data[table_name] = []
    finally:
        con.row_factory = None
    path = out_dir / f"{run_id}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return path


def table(headers: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(headers))) + "|",
            *["| " + " | ".join(str(x) for x in row) + " |" for row in rows]]


def _secs(v) -> str:
    return f"{v:,.0f}" if v else "n/a"


def _picks(r: dict) -> str:
    return " / ".join(str(r["picks"].get(k, 0)) for k in ("ceo", "cfo", "cto"))


def _verdicts(r: dict) -> str:
    c = r["claims"]
    return " / ".join(str(c.get(v, 0)) for v in ("supported", "unverifiable", "contradicted", "miscalculated"))


METRICS = [   # (label, value for one run): shared by both reports so the numbers are computed one way only
    ("Wall-clock time for the whole debate (s)", lambda r: _secs(r["wall"])),
    ("Model-call latency, summed over all calls (s)", lambda r: _secs(r["ms"] / 1000)),
    ("VC turns (opening, questions, closing remark)", lambda r: r["vc_turns"]),
    ("Debate turns (VC + executives + devil)", lambda r: r["debate_turns"]),
    ("API requests: Claude", lambda r: f"{r['p']['claude']['requests']:,}"),
    ("API requests: JEV", lambda r: f"{r['p']['jev']['requests']:,}"),
    ("System-1 requests (claim extraction, verdicts, moderator)", lambda r: f"{r['s1_requests']:,}"),
    ("Claude input tokens (uncached)", lambda r: f"{r['p']['claude']['input']:,}"),
    ("Claude cache-write tokens", lambda r: f"{r['p']['claude']['cache_write']:,}"),
    ("Claude cache-read tokens", lambda r: f"{r['p']['claude']['cache_read']:,}"),
    ("Claude output tokens (incl. thinking)", lambda r: f"{r['p']['claude']['output']:,}"),
    ("JEV input tokens (billed)", lambda r: f"{r['p']['jev']['input']:,}"),
    ("JEV output tokens (reported by JEV, not billed)", lambda r: f"{r['p']['jev']['output']:,}"),
    ("Total tokens processed (all of the above)", lambda r: f"{r['tokens']:,}"),
    ("Cost: Claude (USD)", lambda r: f"${r['p']['claude']['usd']:.4f}"),
    ("Cost: JEV (USD)", lambda r: f"${r['p']['jev']['usd']:.6f}"),
    ("Cost: total (USD)", lambda r: f"${r['usd']:.4f}"),
    ("Cost check", reconciles),
    ("Cost per VC turn (USD)", lambda r: f"${r['usd'] / r['vc_turns']:.4f}" if r["vc_turns"] else "n/a"),
    ("Avg / max words per answer (executives, devil)", lambda r: f"{r['words'][0] or 0:.0f} / {r['words'][1] or 0}"),
    ("Moderator picks for main questions (CEO / CFO / CTO)", _picks),
    ("Claims: supported / unverifiable / contradicted / miscalculated", _verdicts),
    ("Claims settled in code (calculations)", lambda r: r["in_code"]),
    ("Claims escalated to the stronger model", lambda r: r["escalated"]),
    ("VC decision", lambda r: r["decision"]),
]


def versus(runs: list[dict], out: Path) -> None:
    """The latest live optimized run against the latest live all-Opus run. Measured values only."""
    live = [r for r in runs if not r["cfg"].get("dry_run") and r["status"] == "completed"]
    routed = next((r for r in reversed(live) if r["cfg"].get("profile") == "routed"), None)
    base = next((r for r in reversed(live) if r["cfg"].get("profile") == "opus_low"), None)
    if routed is None or base is None:
        missing = "an optimized (routed)" if routed is None else "an all-Opus (opus_low)"
        out.write_text(f"# Optimized vs Opus-only\n\nNo completed live {missing} run is stored yet, so there is "
                       "nothing measured to compare. Run `main.ipynb` with `RUN_COST_BASELINE = True`.\n")
        print(f"wrote {out} (incomplete: {missing} run is missing)")
        return
    rows = [["Models", "JEV (moderator, fact-check verdicts) + Haiku (claim extraction) + Sonnet (debate, "
             "escalations) + Opus (decision)" if routed["p"]["jev"]["requests"] else
             "Haiku (System 1) + Sonnet (debate, escalations) + Opus (decision)",
             "Opus for every call, the moderator included"],
            ["Run", f"`{routed['id']}`", f"`{base['id']}`"]]
    rows += [[label, f(routed), f(base)] for label, f in METRICS]
    saved = base["usd"] - routed["usd"]
    rows.append(["Optimized saves (cost)", f"${saved:.4f} ({saved / base['usd']:.1%} of Opus-only)" if base["usd"] else "n/a", ""])
    if routed["wall"] and base["wall"]:
        rows.append(["Optimized vs Opus-only (wall-clock)",
                     f"{routed['wall'] - base['wall']:+,.0f}s ({routed['wall'] / base['wall'] - 1:+.1%})", ""])
    lines = ["# Optimized (routed) vs Opus-only", "",
             f"Company `{routed['company']}`. Both columns are measured from the recorded API usage of "
             "each run; nothing is estimated. Each meeting runs until the VC says it is ready to decide, so the two runs "
             "can differ in length; compare per-turn figures as well as totals.", ""]
    lines += table(["Metric", "Optimized (routed)", "Opus only"], rows)
    lines += ["", "Claude input tokens exclude cache writes and reads, which are listed and priced separately; Claude "
              "output tokens include thinking. JEV bills input tokens only ($0.042 per million), so its output tokens "
              "are shown but not priced."]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "reports" / "inference_comparison.md"))
    ap.add_argument("--versus-out", default=str(ROOT / "reports" / "optimized_vs_opus_only.md"))
    ap.add_argument("--db", default=str(ROOT / "data" / "debate.sqlite"))
    ap.add_argument("--raw-dir", default=str(ROOT / "reports" / "raw"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    if "requests" not in {r[1] for r in con.execute("PRAGMA table_info(model_calls)")}:
        con.execute("ALTER TABLE model_calls ADD COLUMN requests INTEGER NOT NULL DEFAULT 1")
    ids = args.run_ids or [r[0] for r in con.execute("SELECT run_id FROM runs ORDER BY rowid")]
    runs = [load(con, i) for i in ids]

    head = ["Metric", *[f"`{r['id']}`" for r in runs]]
    lines = ["# Inference metrics: stored runs compared", "",
             f"Generated from `{Path(args.db).name}` by `scripts/inference_metrics.py`. {len(runs)} runs. Every value is "
             "read from the recorded API usage of each call; nothing is estimated.", ""]
    if any(r["cfg"].get("dry_run") for r in runs):
        lines += ["> A **dry run** uses scripted replies with simulated token counts and costs and no latency. Compare "
                  "latency, cost and quality only between live runs.", ""]
    lines += table(head, [["Kind", *["dry run (simulated)" if r["cfg"].get("dry_run") else "live (billed)" for r in runs]],
                          ["Profile", *[r["cfg"].get("profile") for r in runs]],
                          ["Status", *[r["status"] for r in runs]]]
                   + [[label, *[f(r) for r in runs]] for label, f in METRICS]) + [""]

    for r in runs:
        if r["cfg"].get("dry_run"):
            continue
        lines += [f"## Live run `{r['id']}` by tier and model", ""] + table(
            ["Tier", "Model", "Requests", "Input tok", "Cache-write tok", "Cache-read tok", "Output tok",
             "Latency (s)", "Cost (USD)", "Share of cost"],
            [[c["tier"], f"`{c['model']}`", f"{c['requests']:,}", f"{c['input']:,}", f"{c['cache_write']:,}",
              f"{c['cache_read']:,}", f"{c['output']:,}" + (" (not billed)" if _provider(c["model"]) == "jev" else ""),
              f"{c['ms'] / 1000:,.1f}", f"${c['usd']:.6f}", f"{c['usd'] / r['usd']:.1%}" if r["usd"] else "-"]
             for c in sorted(r["calls"], key=lambda c: -(c["usd"] or 0))]) + [""]
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out}")
    versus(runs, Path(args.versus_out))
    for r in runs:
        if not r["cfg"].get("dry_run"):
            print("wrote", dump_raw(con, r["id"], Path(args.raw_dir)))


if __name__ == "__main__":
    main()
