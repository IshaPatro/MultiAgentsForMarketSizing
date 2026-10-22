"""Write reports/inference_comparison.md: inference metrics of stored runs side by side, from data/debate.sqlite.

    python3 scripts/inference_metrics.py [RUN_ID ...] [--out reports/inference_comparison.md]

With no run ids, every stored run is compared. Scripted dry runs are included but marked: their tokens and costs
are simulated, so only live runs are comparable. A live routed run also gets a what-if estimate of the same
tokens priced entirely on Opus (the real baseline is `main.ipynb` with RUN_COST_BASELINE = True).
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PRICES = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text())["prices"]


def price(model: str, i: int, o: int, cr: int, cw: int) -> float:
    p = PRICES[model]
    return (i * p["input"] + o * p["output"] + cr * p["cache_read"] + cw * p["cache_write"]) / 1e6


def load(con: sqlite3.Connection, run_id: str) -> dict:
    run = con.execute("SELECT status, config_json, total_cost_usd, started_at, ended_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        sys.exit(f"unknown run {run_id}")
    cfg = json.loads(run[1])
    calls = con.execute("SELECT tier, model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cache_read_tokens), "
                        "SUM(cache_write_tokens), SUM(latency_ms), SUM(cost_usd) FROM model_calls WHERE run_id=? "
                        "GROUP BY tier, model", (run_id,)).fetchall()
    turns = con.execute("SELECT COUNT(*), AVG(word_count), MAX(word_count), SUM(retries), SUM(trimmed), "
                        "SUM(is_follow_up), SUM(invalid_citations) FROM turns WHERE run_id=?", (run_id,)).fetchone()
    claims = con.execute("SELECT COUNT(*), SUM(escalated), SUM(verdict='supported'), SUM(verdict='unverifiable'), "
                         "SUM(verdict='contradicted'), SUM(verdict='miscalculated') FROM claims WHERE run_id=?",
                         (run_id,)).fetchone()
    decision = con.execute("SELECT decision_json FROM decisions WHERE run_id=?", (run_id,)).fetchone()
    picks = dict(con.execute("SELECT addressed_to, COUNT(*) FROM turns WHERE run_id=? AND speaker='moderator' "
                             "AND is_follow_up=0 GROUP BY addressed_to", (run_id,)).fetchall())
    wall = None
    if run[3] and run[4]:
        from datetime import datetime
        wall = (datetime.fromisoformat(run[4]) - datetime.fromisoformat(run[3])).total_seconds()
    jev = con.execute("SELECT COUNT(*) FROM model_calls WHERE run_id=? AND model LIKE 'jev%'", (run_id,)).fetchone()[0]
    return {"id": run_id, "status": run[0], "cfg": cfg, "cost": run[2] or 0.0, "calls": calls, "turns": turns,
            "claims": claims, "decision": json.loads(decision[0]).get("decision") if decision else "-",
            "picks": picks, "wall": wall, "jev_calls": jev}


def dump_raw(con: sqlite3.Connection, run_id: str, out_dir: Path) -> Path:
    """Everything stored for one run (run row, turns, claims, model calls, retrievals, events, decision) as JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con.row_factory = sqlite3.Row
    try:
        data = {"run": dict(con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())}
        for table in ("turns", "claims", "model_calls", "retrievals", "events", "decisions"):
            try:
                data[table] = [dict(r) for r in con.execute(f"SELECT * FROM {table} WHERE run_id=?", (run_id,))]
            except sqlite3.OperationalError:
                data[table] = []
    finally:
        con.row_factory = None
    path = out_dir / f"{run_id}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return path


def totals(r: dict) -> dict:
    c = r["calls"]
    t = {k: sum(row[i] or 0 for row in c) for k, i in (("n", 2), ("i", 3), ("o", 4), ("cr", 5), ("cw", 6), ("ms", 7), ("usd", 8))}
    t["s1"] = sum(row[2] for row in c if row[0] in ("fast", "verdict"))
    t["s1_usd"] = sum(row[8] for row in c if row[0] in ("fast", "verdict"))
    t["opus_usd"] = sum(price("claude-opus-5-5", row[3] or 0, row[4] or 0, row[5] or 0, row[6] or 0) for row in c)
    return t


def table(headers: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(headers))) + "|",
            *["| " + " | ".join(str(x) for x in row) + " |" for row in rows]]


def versus(con, runs, T, out: Path) -> None:
    """Optimized (routed) vs Opus-only, side by side. Uses a measured opus_low run when one exists, else an estimate."""
    live = [r for r in runs if not r["cfg"].get("dry_run") and r["status"] == "completed"]
    routed = next((r for r in reversed(live) if r["cfg"].get("profile") == "routed"), None)
    if routed is None:
        out.write_text("# Optimized vs Opus-only\n\nNo completed live routed run is stored yet.\n")
        print(f"wrote {out} (no live routed run)")
        return
    base = next((r for r in reversed(live) if r["cfg"].get("profile") == "opus_low"
                 and r["cfg"]["limits"]["max_rounds"] == routed["cfg"]["limits"]["max_rounds"]), None)
    tr = T[routed["id"]]
    if base:
        tb, measured = T[base["id"]], True
        b = {"calls": tb["n"], "i": tb["i"], "o": tb["o"], "cr": tb["cr"], "ms": tb["ms"], "cost": base["cost"],
             "turns": base["turns"][0], "claims": base["claims"], "decision": base["decision"], "avg": base["turns"][1],
             "wall": base["wall"], "picks": base["picks"], "jev": base["jev_calls"]}
    else:
        measured = False
        b = {"calls": tr["n"], "i": tr["i"], "o": tr["o"], "cr": tr["cr"], "ms": None, "cost": tr["opus_usd"],
             "turns": routed["turns"][0], "claims": None, "decision": None, "avg": None,
             "wall": None, "picks": None, "jev": 0}
    saved = b["cost"] - routed["cost"]
    if measured and b["wall"] and routed["wall"]:
        faster = f"{routed['wall'] / b['wall'] - 1:+.0%}"
    na = "not measured"
    rows = [
        ["Models", "JEV (fact-check verdicts, moderator) + Haiku (claim extraction) + Sonnet (debate, escalations) + Opus (decision)"
         if routed["jev_calls"] else "Haiku (System 1) + Sonnet (debate, escalations) + Opus (decision)", "Opus for every call, including the moderator"],
        ["Run", f"`{routed['id']}` (measured)", f"`{base['id']}` (measured)" if measured else "estimate: same tokens at Opus prices"],
        ["Model calls", tr["n"], b["calls"]],
        ["Input tokens", f"{tr['i']:,}", f"{b['i']:,}"],
        ["Output tokens", f"{tr['o']:,}", f"{b['o']:,}"],
        ["Cache-read tokens", f"{tr['cr']:,}", f"{b['cr']:,}"],
        ["Wall-clock time for the whole debate (s)", f"{routed['wall']:,.0f}" if routed["wall"] else na, f"{b['wall']:,.0f}" if b["wall"] else na],
        ["Total call latency, summed over calls (s)", f"{tr['ms'] / 1000:,.0f}", f"{b['ms'] / 1000:,.0f}" if b["ms"] else na],
        ["Total tokens (input + output)", f"{tr['i'] + tr['o']:,}", f"{b['i'] + b['o']:,}"],
        ["JEV calls", routed["jev_calls"], b["jev"] if measured else "0"],
        ["Moderator picks (CEO / CFO / CTO)", " / ".join(str(routed["picks"].get(k, 0)) for k in ("ceo", "cfo", "cto")),
         " / ".join(str(b["picks"].get(k, 0)) for k in ("ceo", "cfo", "cto")) if b["picks"] is not None else na],
        ["Cost (USD)", f"${routed['cost']:.4f}", f"${b['cost']:.4f}" + ("" if measured else " (estimate)")],
        ["Cost per turn (USD)", f"${routed['cost'] / routed['turns'][0]:.4f}", f"${b['cost'] / b['turns']:.4f}"],
        ["Avg words per turn", f"{routed['turns'][1]:.1f}", f"{b['avg']:.1f}" if b["avg"] else na],
        ["Claims extracted (supported)", f"{routed['claims'][0]} ({routed['claims'][2]})",
         f"{b['claims'][0]} ({b['claims'][2]})" if b["claims"] else na],
        ["VC decision", routed["decision"], b["decision"] or na],
        ["Saving from routing", f"${saved:.4f} ({saved / b['cost']:.0%} of Opus-only)", ""]]
    if measured and b["wall"] and routed["wall"]:
        rows.append(["Wall-clock difference (optimized vs Opus-only)", f"{routed['wall'] - b['wall']:+,.0f}s ({faster})", ""])
    lines = ["# Optimized (routed) vs Opus-only", "",
             f"Company {routed['cfg'].get('company_id', 'biobot')}, {routed['cfg']['limits']['max_rounds']} rounds. "
             + ("Both columns are measured runs." if measured else
                "**The Opus-only column is an estimate**: this run's recorded tokens priced at Opus rates. A real "
                "Opus-only run would use different tokens, so latency, quality and claims are not measured."), ""]
    lines += table(["Metric", "Optimized (routed)", "Opus only"], rows)
    if not measured:
        lines += ["", "To measure it: set `RUN_COST_BASELINE = True` in `main.ipynb` (raise `limits.max_cost_usd_per_run` in "
                  "`configs/models.yaml` first: an Opus-only run is estimated at more than the current $1 cap, which would "
                  "end it as `budget_exceeded`), run it, then re-run `scripts/inference_metrics.py`; this file switches to "
                  "measured numbers by itself."]
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({'measured' if measured else 'estimated'} baseline)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_ids", nargs="*")
    ap.add_argument("--out", default=str(ROOT / "reports" / "inference_comparison.md"))
    ap.add_argument("--versus-out", default=str(ROOT / "reports" / "optimized_vs_opus_only.md"))
    args = ap.parse_args()
    con = sqlite3.connect(ROOT / "data" / "debate.sqlite")
    ids = args.run_ids or [r[0] for r in con.execute("SELECT run_id FROM runs ORDER BY rowid")]
    runs = [load(con, i) for i in ids]
    T = {r["id"]: totals(r) for r in runs}
    kind = lambda r: "dry run (simulated)" if r["cfg"].get("dry_run") else "live (billed)"  # noqa: E731

    def row(label, f):
        return [label, *[f(r, T[r["id"]]) for r in runs]]

    head = ["Metric", *[f"`{r['id']}`" for r in runs]]
    lines = ["# Inference metrics: stored runs compared", "",
             f"Generated from `data/debate.sqlite` by `scripts/inference_metrics.py`. {len(runs)} runs.", ""]
    if any(r["cfg"].get("dry_run") for r in runs):
        lines += ["> A **dry run** uses scripted replies with simulated token counts and costs, no latency and no "
                  "real arguments. It shows the pipeline's shape, not model behaviour: compare latency, cost and "
                  "quality only between live runs.", ""]
    lines += ["## Run summary", ""] + table(head, [
        row("Kind", lambda r, t: kind(r)),
        row("Profile", lambda r, t: r["cfg"].get("profile")),
        row("Status", lambda r, t: r["status"]),
        row("Turns", lambda r, t: r["turns"][0]),
        row("Follow-up turns", lambda r, t: r["turns"][5] or 0),
        row("Avg words per turn (max)", lambda r, t: f"{r['turns'][1]:.1f} ({r['turns'][2]})"),
        row("Over-limit rewrites / trims", lambda r, t: f"{r['turns'][3] or 0} / {r['turns'][4] or 0}"),
        row("Moderator picks (CEO / CFO / CTO)", lambda r, t: " / ".join(str(r["picks"].get(k, 0)) for k in ("ceo", "cfo", "cto"))),
        row("VC decision", lambda r, t: r["decision"])]) + [""]
    lines += ["## Inference", ""] + table(head, [
        row("Model calls", lambda r, t: t["n"]),
        row("System-1 calls (share)", lambda r, t: f"{t['s1']} ({t['s1'] / t['n']:.0%})" if t["n"] else "-"),
        row("Input tokens", lambda r, t: f"{t['i']:,}"),
        row("Output tokens", lambda r, t: f"{t['o']:,}"),
        row("Cache-read tokens (share of all input)", lambda r, t: f"{t['cr']:,} ({t['cr'] / (t['i'] + t['cr'] + t['cw']):.0%})" if t["n"] else "-"),
        row("Wall-clock time (s)", lambda r, t: f"{r['wall']:,.0f}" if r["wall"] else "n/a"),
        row("Total call latency (s)", lambda r, t: f"{t['ms'] / 1000:,.0f}" if t["ms"] else "n/a"),
        row("Avg latency per call (s)", lambda r, t: f"{t['ms'] / t['n'] / 1000:.1f}" if t["ms"] else "n/a"),
        row("Cost (USD)", lambda r, t: f"${r['cost']:.4f}"),
        row("Cost per turn (USD)", lambda r, t: f"${r['cost'] / r['turns'][0]:.4f}"),
        row("System-1 share of cost", lambda r, t: f"{t['s1_usd'] / t['usd']:.1%}" if t["usd"] else "-")]) + [""]
    lines += ["## Fact-checking", ""] + table(head, [
        row("Claims extracted", lambda r, t: r["claims"][0]),
        row("Supported", lambda r, t: r["claims"][2] or 0),
        row("Unverifiable", lambda r, t: r["claims"][3] or 0),
        row("Contradicted", lambda r, t: r["claims"][4] or 0),
        row("Miscalculated", lambda r, t: r["claims"][5] or 0),
        row("Escalated to the stronger model", lambda r, t: r["claims"][1] or 0),
        row("Invalid citations", lambda r, t: r["turns"][6] or 0)]) + [""]

    for r in runs:
        if r["cfg"].get("dry_run"):
            continue
        t = T[r["id"]]
        lines += [f"## Live run `{r['id']}`: cost and latency by tier", ""] + table(
            ["Tier", "Model", "Calls", "Input tok", "Output tok", "Cache-read tok", "Latency (s)", "Cost (USD)", "Share of cost"],
            [[tier, f"`{m}`", n, f"{i:,}", f"{o:,}", f"{cr:,}", f"{ms / 1000:,.0f}", f"${usd:.4f}", f"{usd / t['usd']:.1%}"]
             for tier, m, n, i, o, cr, cw, ms, usd in sorted(r["calls"], key=lambda x: -x[8])]) + [""]
        saved = t["opus_usd"] - t["usd"]
        lines += [f"### What-if: the same tokens, all on Opus (estimate)", ""] + table(
            ["Setup", "Cost (USD)", "Note"],
            [["Routed (recorded)", f"${t['usd']:.4f}", "Haiku for System 1, Sonnet for debate, Opus for the decision"],
             ["All Opus, same tokens", f"${t['opus_usd']:.4f}", "Opus list prices applied to this run's recorded tokens"],
             ["Difference", f"${saved:.4f} ({saved / t['opus_usd']:.0%} cheaper routed)", "Estimate only"]]) + [
            "", "This is not a measured baseline: a real all-Opus run produces different tokens (different thinking "
            "length, answers and claims) and slower calls. Run `main.ipynb` with `RUN_COST_BASELINE = True`, then "
            "`debate compare --routed RUN --baseline OPUS_RUN`, for the measured comparison.", ""]
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out}")
    versus(con, runs, T, Path(args.versus_out))
    raw = ROOT / "reports" / "raw"
    for r in runs:
        if not r["cfg"].get("dry_run"):
            print("wrote", dump_raw(con, r["id"], raw).relative_to(ROOT))


if __name__ == "__main__":
    main()
