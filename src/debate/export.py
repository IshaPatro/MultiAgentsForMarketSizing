"""Export saved runs to Markdown for a static site.

One page per run (front matter + transcript with maths, assumptions and citations + fact-checks + decision +
cost and audit metadata) and an index page. Pages are generated only from the SQLite records, never from
model calls, and never include source chunk text: the HBS case is licensed, so citations show the source title.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from debate.agenda import AGENDA
from debate.config import load_company, load_corpus
from debate.nodes.agents import LABEL
from debate.rag.corpus import public_slug
from debate.storage.sqlite_store import SqliteStore

VERDICT_ICON = {"supported": "✅", "contradicted": "❌", "unverifiable": "❔", "miscalculated": "🧮",
                "not_checked": "·"}
TAGLINE = "Where bold ideas meet hard questions."


def _company_name(company_id: str) -> str:
    try:
        return load_company(company_id).name
    except (KeyError, FileNotFoundError, ValueError):
        return company_id.replace("_", " ").title()


def _rows(store: SqliteStore, sql: str, args: tuple = ()) -> list[dict]:
    cur = store.conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _cell(value) -> str:
    """Make a value safe inside a Markdown table cell."""
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def _yaml_str(value) -> str:
    # JSON strings are valid YAML double-quoted scalars; keep "—" and "→" readable rather than \u-escaped.
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


class _Sources:
    """chunk id -> human-readable source label (corpus layout from configs/corpus/<company>.yaml)."""

    def __init__(self, company_id: str):
        try:
            m = load_corpus(company_id)
            case = m.case.title
            self.sources = [(f"{company_id}_case_", case), (f"{company_id}_fact_", f"Curated fact ({case})"),
                            (f"{company_id}_caption_", f"Exhibit caption ({case})"),
                            (f"{company_id}_note_", f"Endnote ({case})"),
                            (f"{company_id}_assume_", "Sizing input - not from the case (assumption or public source)"),
                            (f"{company_id}_sizing_", "Sizing model (facts + assumptions)")]
            for p in m.public_sources:   # leads first: their prefix is longer than the ledger's
                self.sources += [(f"{company_id}_{p.id}_{public_slug(sec)}_", f"Unverified lead ({p.title})")
                                 for sec, cfg in p.sections.items() if cfg.reliability == "lead"]
                self.sources.append((f"{company_id}_{p.id}_", f"{p.title} (public sources, checked {p.checked})"))
            self.sources += [(f"{company_id}_{e.id}_", f"Exhibit {e.number} ({case})") for e in m.exhibits]
        except KeyError:
            self.sources = []

    def label(self, chunk_id: str) -> str:
        for prefix, title in self.sources:
            if chunk_id.startswith(prefix):
                page = re.search(r"_p(\d+)_", chunk_id)
                return f"{title}, p.{page.group(1)}" if page else title
        return "source unavailable"


def run_markdown(store: SqliteStore, run_id: str) -> str:
    run = _rows(store, "SELECT * FROM runs WHERE run_id=?", (run_id,))
    if not run:
        raise ValueError(f"unknown run {run_id}")
    run = run[0]
    config = json.loads(run["config_json"])
    turns = _rows(store, "SELECT * FROM turns WHERE run_id=? ORDER BY seq", (run_id,))
    claims = _rows(store, "SELECT * FROM claims WHERE run_id=? ORDER BY turn_seq, claim_id", (run_id,))
    events = _rows(store, "SELECT * FROM events WHERE run_id=? ORDER BY created_at", (run_id,))
    calls = _rows(store, "SELECT role, tier, model, COUNT(*) AS n, SUM(input_tokens) AS input_tokens, "
                         "SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens, "
                         "SUM(latency_ms) AS latency_ms, SUM(cost_usd) AS cost_usd FROM model_calls "
                         "WHERE run_id=? GROUP BY role, tier, model ORDER BY cost_usd DESC", (run_id,))
    retrievals = _rows(store, "SELECT turn_seq, kind, chunk_ids FROM retrievals WHERE run_id=? ORDER BY rowid",
                       (run_id,))
    decision = store.get_decision(run_id)
    sources = _Sources(run["company_id"])
    models = config.get("models", {}).get("tiers", {})

    title = f"{_company_name(run['company_id'])} · NextGen Boardroom"
    fm = {"title": title, "run_id": run_id, "company": run["company_id"],
          "status": run["status"], "started_at": run["started_at"],
          "ended_at": run["ended_at"], "decision": (decision or {}).get("decision"),
          "conviction": (decision or {}).get("conviction"), "total_cost_usd": round(run["total_cost_usd"] or 0, 6),
          "profile": config.get("profile"), "dry_run": bool(config.get("dry_run")),
          "prompt_version": config.get("prompt_version"),
          "rounds": config.get("limits", {}).get("max_rounds"),
          "debate_model": models.get("debate", {}).get("model"), "system1_model": models.get("verdict", models.get("fast", {})).get("model"),
          "decision_model": models.get("decision", {}).get("model")}
    out = ["---"] + [f"{k}: {_yaml_str(v) if isinstance(v, str) or v is None else json.dumps(v)}"
                     for k, v in fm.items()] + ["---", "", f"# {title}", "", f"*{TAGLINE}*", ""]
    if config.get("dry_run"):
        out += ["> **Dry run.** Replies are scripted placeholders and costs are estimates, not model output.", ""]

    verdicts = Counter(c["verdict"] for c in claims)
    tokens_in = sum(c["input_tokens"] or 0 for c in calls)
    tokens_out = sum(c["output_tokens"] or 0 for c in calls)
    n_calls = sum(c["n"] for c in calls)
    s1 = sum(c["n"] for c in calls if c["tier"] in ("fast", "verdict") and c["model"] != "claude-opus-5-5")
    out += ["## Summary", "", "| | |", "|---|---|",
            f"| Status | {run['status']} |",
            f"| Decision | {_cell((decision or {}).get('decision', '—'))} |",
            f"| Turns | {len(turns)} |",
            f"| Claims | {', '.join(f'{n} {v}' for v, n in verdicts.most_common()) or '—'} |",
            f"| Model calls | {n_calls} ({s1} System-1) |",
            f"| Tokens | {tokens_in:,} in / {tokens_out:,} out |",
            f"| Cost | ${run['total_cost_usd'] or 0:.4f}{' (estimated)' if config.get('dry_run') else ''} |",
            f"| Prompt version | `{config.get('prompt_version', '')}` |", ""]

    claims_by_turn: dict[int, list[dict]] = {}
    for c in claims:
        claims_by_turn.setdefault(c["turn_seq"], []).append(c)
    out += ["## Transcript", ""]
    current_topic = None
    for t in turns:
        topic = t.get("topic") or ""
        if topic and topic != current_topic:  # one heading per agenda topic (question, answers, follow-up)
            out += [f"## Topic: {AGENDA[topic].title if topic in AGENDA else topic}", ""]
            current_topic = topic
        flags = [f for f, on in (("trimmed", t["trimmed"]),
                                 (f"rewritten ×{t['retries']}", t["retries"]),
                                 ("follow-up", t.get("is_follow_up") and t["speaker"] == "vc")) if on]
        out += [f"### {t['seq']}. {LABEL.get(t['speaker'], t['speaker'])} → "
                f"{LABEL.get(t['addressed_to'], t['addressed_to'])} (round {t['round']})"
                + (f" — _{', '.join(flags)}_" if flags else ""), "", t["content"], ""]
        calc = t.get("calculation") or ""
        if calc:
            ok = t.get("calculation_ok")
            status = {1: "✅ checks out", 0: "❌ does not add up", None: "not machine-checkable"}[ok]
            out += [f"**Maths** ({status}): `{calc}`", ""]
        assumptions = json.loads(t.get("assumptions_json") or "[]")
        if assumptions:
            out += ["**Assumptions:**", ""] + [f"- {a}" for a in assumptions] + [""]
        estimates = json.loads(t.get("estimates_json") or "[]")
        if estimates:  # e.g. three ways to size the market, side by side
            out += ["| Estimate | Maths | Check | Assumptions |", "|---|---|---|---|"]
            for e in estimates:
                check = {True: "✅", False: "❌ does not add up", None: "—"}[e.get("calculation_ok")]
                out.append(f"| {_cell(e['method'])} | `{_cell(e['calculation'])}` | {check} | "
                           f"{_cell('; '.join(e.get('assumptions') or []))} |")
            out.append("")
        cited = json.loads(t["evidence_ids"] or "[]")
        if cited:
            out += ["**Cites:** " + "; ".join(f"`{e}` — {sources.label(e)}" for e in cited), ""]
        for c in claims_by_turn.get(t["seq"], []):
            if c["verdict"] != "not_checked":
                out.append(f"- {VERDICT_ICON.get(c['verdict'], '')} _{c['verdict']}_: {c['text']}"
                           + (f" — {c['note']}" if c["note"] else ""))
        if claims_by_turn.get(t["seq"]):
            out.append("")
        out += [f"<sub>{t['model']} · {t['input_tokens']} in / {t['output_tokens']} out · "
                f"{t['latency_ms']} ms · ${t['cost_usd']:.5f}</sub>", ""]

    out += ["## Fact-checks", ""]
    if claims:
        out += ["| Claim | Speaker | Type | Verdict | Confidence | Checker | Evidence | Note |",
                "|---|---|---|---|---:|---|---|---|"]
        for c in claims:
            conf = "" if c["confidence"] is None else f"{c['confidence']:.2f}"
            ev = ", ".join(f"`{e}`" for e in json.loads(c["evidence_ids"] or "[]"))
            out.append(f"| {_cell(c['text'])} | {LABEL.get(c['speaker'], c['speaker'])} | {c['type']} | "
                       f"{VERDICT_ICON.get(c['verdict'], '')} {c['verdict']} | {conf} | "
                       f"{_cell(c['checker_model'])}{' (escalated)' if c['escalated'] else ''} | {ev} | "
                       f"{_cell(c['note'])} |")
    else:
        out.append("No claims were recorded.")
    out.append("")

    out += ["## VC decision", ""]
    if decision:
        claim_text = {c["claim_id"]: c["text"] for c in claims}
        out += [f"**{decision['decision']}** — conviction {decision['conviction']:.0%}", "",
                "| Rubric | Score | Why |", "|---|---:|---|"]
        for key, score in decision["scores"].items():
            reason = decision.get("score_drivers", {}).get(key, {}).get("reason", "")
            out.append(f"| {key.replace('_', ' ').title()} | {score}/5 | {_cell(reason)} |")
        out += ["", "**For:**", ""] + [f"- {r}" for r in decision["key_reasons_for"]]
        out += ["", "**Against:**", ""] + [f"- {r}" for r in decision["key_reasons_against"]]
        out += ["", f"**Terms:** {decision['deal_terms_sketch']}", "",
                "**Would change my mind:**", ""] + [f"- {r}" for r in decision["what_would_change_my_mind"]]
        if decision.get("cited_claim_ids"):
            out += ["", "**Checked claims the decision relies on:**", ""]
            out += [f"- `{cid}` {claim_text.get(cid, '')}" for cid in decision["cited_claim_ids"]]
        out.append("")
    else:
        out += ["No decision was recorded for this run.", ""]

    out += ["## Cost and model calls", ""]
    if calls:
        out += ["| Role | Tier | Model | Calls | Input | Output | Cache read | Latency (s) | Cost |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|"]
        for c in calls:
            out.append(f"| {c['role']} | {c['tier']} | {c['model']} | {c['n']} | {c['input_tokens']:,} | "
                       f"{c['output_tokens']:,} | {c['cache_read_tokens']:,} | {c['latency_ms'] / 1000:.1f} | "
                       f"${c['cost_usd']:.5f} |")
    else:
        out.append("No model calls were recorded.")
    out.append("")

    out += ["## Audit", "",
            f"- Retrievals: {len(retrievals)} "
            f"({sum(r['kind'] == 'context' for r in retrievals)} for answers, "
            f"{sum(r['kind'] == 'factcheck' for r in retrievals)} for fact-checks)",
            f"- Turns with citations that were not retrieved: {sum(t['invalid_citations'] > 0 for t in turns)}",
            f"- Cross-agent memory access attempts: {sum(e['type'] == 'leak_attempt' for e in events)}", ""]
    notable = [e for e in events if e["type"] not in ("fact_check_cost",)]
    if notable:
        out += ["### Events", ""]
        for e in notable:
            payload = json.loads(e["payload_json"])
            out.append(f"- `{e['created_at']}` **{e['type']}** {_cell(json.dumps(payload))}")
        out.append("")
    return "\n".join(out)


def export_runs(store: SqliteStore, out_dir: Path, run_ids: list[str] | None = None,
                include_dry_runs: bool = False) -> list[Path]:
    """Write runs/<run_id>.md for each run plus index.md; returns the written paths."""
    out_dir = Path(out_dir)
    (out_dir / "runs").mkdir(parents=True, exist_ok=True)
    all_runs = _rows(store, "SELECT run_id, company_id, status, started_at, total_cost_usd, config_json "
                            "FROM runs ORDER BY started_at")
    selected = [r for r in all_runs if (run_ids is None or r["run_id"] in run_ids)
                and (include_dry_runs or run_ids is not None or not json.loads(r["config_json"]).get("dry_run"))]
    written = []
    for r in selected:
        path = out_dir / "runs" / f"{r['run_id']}.md"
        path.write_text(run_markdown(store, r["run_id"]))
        written.append(path)

    index = ["---", 'title: "NextGen Boardroom"', "---", "", "# NextGen Boardroom", "",
             f"*{TAGLINE}*", "", "## Company conversations", "",
             "| Company | Status | Decision | Messages | Cost | Started |",
             "|---|---|---|---:|---:|---|"]
    for r in selected:
        d = store.get_decision(r["run_id"]) or {}
        n = store.conn.execute("SELECT COUNT(*) FROM turns WHERE run_id=?", (r["run_id"],)).fetchone()[0]
        cfg = json.loads(r["config_json"])
        tag = " (dry run)" if cfg.get("dry_run") else ""
        index.append(f"| [{_company_name(r['company_id'])}](runs/{r['run_id']}.md){tag} | "
                     f"{r['status']} | {d.get('decision', '—')} | {n} | ${r['total_cost_usd'] or 0:.4f} | "
                     f"{r['started_at']} |")
    reports = sorted(p for p in out_dir.glob("*.md") if p.name != "index.md")
    if reports:
        index += ["", "## Reports", ""] + [f"- [{p.stem.replace('_', ' ')}]({p.name})" for p in reports]
    index_path = out_dir / "index.md"
    index_path.write_text("\n".join(index) + "\n")
    return written + [index_path]
