"""Evaluation dashboard built from saved run records (no model calls)."""
from __future__ import annotations

from debate.config import ROOT
from debate.prompts import prompt_version as current_prompt_version
from debate.storage.sqlite_store import SqliteStore

VIEWS = ROOT / "db" / "003_eval_views.sql"


def install_views(store: SqliteStore) -> None:
    store.conn.executescript("DROP VIEW IF EXISTS eval_decision_stability; DROP VIEW IF EXISTS eval_run_metrics;")
    store.conn.executescript(VIEWS.read_text())


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "n/a"


def build_dashboard(store: SqliteStore, company: str = "biobot", version: str | None = None) -> str:
    install_views(store)
    version = version or current_prompt_version()
    store.conn.row_factory = None
    cols = [d[0] for d in store.conn.execute("SELECT * FROM eval_run_metrics LIMIT 0").description]
    all_rows = [dict(zip(cols, row)) for row in store.conn.execute(
        "SELECT * FROM eval_run_metrics WHERE company_id=? AND status='completed' "
        "AND dry_run=0 AND decision IS NOT NULL ORDER BY run_id", (company,))]
    rows = [r for r in all_rows if r["prompt_version"] == version]
    totals = {k: sum(int(r[k] or 0) for r in rows) for k in (
        "turns", "over_word_limit", "retrievals", "memory_leak_attempts", "factual_claims",
        "cited_factual_claims", "miscalculations", "turns_with_invalid_citations")}
    cost = sum(float(r["model_cost_usd"] or 0) for r in rows)
    lines = [f"# {company.title()} evaluation dashboard", "",
             f"Prompt version `{version}`: {len(rows)} completed live runs; "
             f"{len(all_rows) - len(rows)} runs with other prompt versions excluded. "
             "Generated from saved SQLite run, turn, claim, retrieval, event, decision and model-call records.",
             "", "## Targets", "", "| Metric | Observed | Target | Status |", "|---|---:|---:|---|"]

    def metric(name: str, observed: str, target: str, status: str):
        lines.append(f"| {name} | {observed} | {target} | {status} |")

    metric("Cross-agent memory access attempts", str(totals["memory_leak_attempts"]), "0",
           "PASS" if totals["memory_leak_attempts"] == 0 and rows else "PENDING" if not rows else "FAIL")
    metric("Over-limit turns", str(totals["over_word_limit"]), "0",
           "PASS" if totals["over_word_limit"] == 0 and totals["turns"] else "PENDING" if not totals["turns"] else "FAIL")
    grounded, factual = totals["cited_factual_claims"], totals["factual_claims"]
    metric("Factual claims with valid retrieved citations", f"{_pct(grounded, factual)} ({grounded}/{factual})", "≥95%",
           "PASS" if factual and grounded / factual >= .95 else "FAIL" if factual else "PENDING")
    metric("Calculations that do not add up", str(totals["miscalculations"]), "0",
           "PASS" if totals["miscalculations"] == 0 and rows else "PENDING" if not rows else "FAIL")

    store.conn.row_factory = None
    stability = [dict(zip(("profile", "with_rag", "no_rag", "rounds", "runs", "invest", "pass", "conditional"), item))
                 for item in store.conn.execute(
        "SELECT profile, with_rag, no_rag, max_rounds, runs, invest, pass, conditional "
        "FROM eval_decision_stability WHERE company_id=? AND dry_run=0 AND prompt_version=? "
        "ORDER BY runs DESC", (company, version)).fetchall()]
    eligible = [s for s in stability if s["runs"] >= 5]
    stable = [s for s in eligible if max(s[k] for k in ("invest", "pass", "conditional")) >= 4]
    metric("Decision stability", "; ".join(
        f"{max(s[k] for k in ('invest', 'pass', 'conditional'))}/{s['runs']} ({s['profile']}, {s['rounds']} rounds)"
        for s in eligible) or "<5 comparable runs", "≥4/5",
           "PASS" if stable else "FAIL" if eligible else "PENDING")
    metric("Recorded model cost", f"${cost:.4f} across {len(rows)} runs", "measure", "MEASURED" if rows else "PENDING")
    lines.extend(["", "## Live runs", "", "| Run | Decision | Turns | Factual citation rate | Cost |",
                  "|---|---|---:|---:|---:|"])
    for r in rows:
        lines.append(f"| `{r['run_id']}` | {r['decision'] or '—'} | {r['turns']} | "
                     f"{_pct(r['cited_factual_claims'], r['factual_claims'])} | ${r['model_cost_usd']:.4f} |")
    lines.extend(["", "## Comparable decision groups", "",
                  "| Profile | Rounds | RAG flags | Runs | Invest / Pass / Conditional |", "|---|---:|---|---:|---:|"])
    for s in stability:
        lines.append(f"| {s['profile']} | {s['rounds']} | with={s['with_rag']}, no={s['no_rag']} | {s['runs']} | "
                     f"{s['invest']} / {s['pass']} / {s['conditional']} |")
    lines.extend(["", "## Interpretation", "",
                  "Grounding counts all extracted factual and numeric claims; a claim marked unverifiable does not "
                  "count as grounded. Citation validity checks every claim evidence ID against retrieval records "
                  "for the same turn. Decision stability groups runs by model profile, prompt version and round "
                  "count; five comparable live runs are required before grading it.", ""])
    return "\n".join(lines)
