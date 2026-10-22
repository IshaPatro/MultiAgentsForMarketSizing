"""`debate run --company biobot [--rounds 9] [--dry-run]` and supporting commands."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from debate.config import ROOT, ModelsConfig, load_company, load_env, load_models
from debate.graph import build_graph
from debate.llm.client import LLMError
from debate.llm.router import ModelRouter, configure_profile
from debate.memory.setup import persistence
from debate.memory.views import MemoryView, remember_run
from debate.nodes.agents import LABEL, BudgetExceeded, Deps, support_lines
from debate.llm.jev import DEFAULT_MODEL
from debate.prompts import prompt_version
from debate.state import Turn
from debate.storage.sqlite_store import SqliteStore

DATA = ROOT / "data"


def _print_turn(t: Turn) -> None:
    flags = (f" [rewritten x{t.retries}]" if t.retries else "") + (" [trimmed]" if t.trimmed else "")
    flags += " [follow-up]" if t.follow_up and t.speaker == "vc" else ""
    print(f"\n[{t.seq}] {LABEL[t.speaker]} -> {LABEL.get(t.addressed_to, t.addressed_to)}{flags}")
    print(f"    {t.content}")
    if t.evidence_ids or t.invalid_citations:
        print(f"    cites: {', '.join(t.evidence_ids) or '-'}" + (f"  (+{t.invalid_citations} invalid)" if t.invalid_citations else ""))
    for line in support_lines(t.model_dump()):
        print(f"    {line}")
    u = t.usage
    print(f"    ({u.input_tokens} in + {u.cache_write_tokens} cache-write + {u.cache_read_tokens} cache-read / "
          f"{u.output_tokens} out, {u.latency_ms} ms, ${u.cost_usd:.5f})")


def build_corpus_cmd(args: argparse.Namespace) -> int:
    """PDF -> dated topic files, exhibit tables, endnotes; validates curated facts. No model calls."""
    from debate.config import load_corpus
    from debate.rag.corpus import build_corpus, corpus_dir

    manifest = load_corpus(args.company)
    stats = build_corpus(manifest)
    out = corpus_dir(manifest)
    print(f"built {out}: {stats['narrative_chunks']} narrative chunks from {stats['paragraphs']} paragraphs, "
          f"{stats['exhibits']} exhibit tables, {stats['captions']} captions, {stats['endnotes']} dated endnotes, "
          f"{stats['public_items']} dated public-ledger items")
    for line in stats["reconciliation"]:
        print(f"  exhibit check: {line}")
    print(f"index and sizing lineage: {out / 'README.md'}")
    return 0


def ingest_cmd(args: argparse.Namespace) -> int:
    """Validate and embed the built corpus. Local embeddings only: no model API calls."""
    from debate.config import load_corpus
    from debate.rag import db
    from debate.rag.embeddings import FastEmbedder
    from debate.rag.ingest import ingest

    load_env()
    manifest = load_corpus(args.company)
    try:
        conn = db.connect()
    except Exception as e:  # noqa: BLE001 - any connection failure gets the same hint
        print(f"error: cannot reach Postgres ({e}). Start it with: docker compose up -d", file=sys.stderr)
        return 2
    db.ensure_schema(conn)
    try:
        stats = ingest(manifest, conn, FastEmbedder())
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    kinds = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(stats["by_type"].items()))
    print(f"ingested {stats['chunks']} dated chunks ({stats['words']} words) for {args.company}: {kinds}")
    return 0


def run(args: argparse.Namespace) -> int:
    resuming = bool(getattr(args, "resume_run_id", None))
    if not resuming and args.with_rag and args.no_rag:
        print("error: --with-rag and --no-rag cannot be used together", file=sys.stderr)
        return 2
    load_env()
    store = SqliteStore(DATA / "debate.sqlite")
    if resuming:
        saved = store.get_run(args.resume_run_id)
        if not saved or saved["status"] not in ("paused", "running", "interrupted", "error") or saved["config"].get("checkpoint_backend") != "postgres":
            print("error: run is not resumable from a Postgres checkpoint", file=sys.stderr)
            return 2
        settings = saved["config"]
        cfg = ModelsConfig.model_validate(settings["models"])
        company = load_company(saved["company_id"])
        run_id, thread_id = saved["run_id"], saved["thread_id"]
        dry_run, with_rag, no_rag = settings["dry_run"], settings["with_rag"], settings["no_rag"]
        initial = None
        spent_before = store.call_stats(run_id)["cost_usd"]
    else:
        jev = os.environ.get("JEV_API_KEY") and args.profile == "routed" and not args.dry_run
        cfg = configure_profile(load_models(), args.profile, os.environ.get("JEV_MODEL", DEFAULT_MODEL) if jev else None)
        if args.rounds:
            cfg.limits.max_rounds = args.rounds
        company = load_company(args.company)
        run_id = uuid.uuid4().hex[:12]
        thread_id = f"{company.company_id}:{run_id}"  # one LangGraph thread per run
        dry_run, with_rag, no_rag = args.dry_run, args.with_rag, args.no_rag
        initial = {"run_id": run_id, "company_id": company.company_id, "round": 0, "next_speaker": "ceo",
                   "turns": [], "claims": [], "checked_through": 0, "fact_note": "", "decision": None,
                   "agenda_covered": [], "follow_up": None}
        spent_before = 0.0

    if dry_run:
        from debate.llm.scripted import ScriptedClient
        llm = ScriptedClient(cfg)
    else:
        from debate.llm.claude import ClaudeClient
        from debate.llm.jev import DEFAULT_URL, JevClient
        uses_jev = any(t.model.startswith("jev:") for t in cfg.tiers.values())
        llm = ModelRouter(ClaudeClient(cfg), JevClient(os.environ["JEV_API_KEY"], os.environ.get("JEV_BASE_URL", DEFAULT_URL))
                          if uses_jev else None)

    from debate.rag.retriever import NullRetriever, PgRetriever
    if (dry_run and not with_rag) or no_rag:
        retriever = NullRetriever()
    else:
        from debate.rag import db
        from debate.rag.embeddings import FastEmbedder
        try:
            conn = db.connect()
            db.ensure_schema(conn)
            embedder = FastEmbedder()
            count = conn.execute("SELECT count(*) FROM chunks WHERE company_id=%s AND embedding_model=%s",
                                 (company.company_id, embedder.name)).fetchone()[0]
            if not count:
                conn.close()
                print(f"error: no corpus chunks for {company.company_id}; run debate ingest first", file=sys.stderr)
                return 2
            retriever = PgRetriever(conn, embedder)
        except Exception as e:  # noqa: BLE001
            print(f"error: cannot reach Postgres ({e}). Run: docker compose up -d && python -m debate ingest", file=sys.stderr)
            return 2

    if not resuming:
        store.start_run(run_id=run_id, company_id=company.company_id, thread_id=thread_id,
                        config={"models": cfg.model_dump(mode="json"), "limits": cfg.limits.model_dump(),
                                "prompt_version": prompt_version(), "dry_run": dry_run,
                                "with_rag": with_rag, "no_rag": no_rag,
                                "profile": args.profile, "checkpoint_backend": "postgres"})
    deps = Deps(llm=llm, cfg=cfg, company=company, retriever=retriever,
                sinks=[_print_turn, lambda t: store.add_turn(run_id, t)],
                retrieval_sinks=[lambda seq, kind, r: store.add_retrieval(run_id, seq, kind, r)],
                event_sinks=[lambda type_, payload: store.add_event(run_id, type_, payload)],
                claim_sinks=[lambda claims: store.add_claims(run_id, claims)],
                call_sinks=[lambda role, tier, model, usage: store.add_model_call(run_id, role, tier, model, usage)],
                spent_usd=spent_before)

    print(f"{company.name} | run {run_id}"
          f"{' | RESUMING' if resuming else ''}{' | DRY RUN (no model calls)' if dry_run else ''}")
    status = "completed"
    with persistence() as (saver, memory_store):
        deps.vc_memory = MemoryView(memory_store, "vc", deps.event)
        try:
            graph = build_graph(deps, saver, interrupt_after=[args.pause_after] if not resuming and args.pause_after else None)
            # 5 graph steps per round (question, moderator, answer, attack, fact-check) plus headroom.
            limit = 5 * cfg.limits.max_rounds + 10
            graph_config = {"configurable": {"thread_id": thread_id}, "recursion_limit": limit}
            if resuming and not graph.get_state(graph_config).values:
                print("error: no saved checkpoint for this run", file=sys.stderr)
                return 2
            graph.invoke(initial, graph_config)
            snapshot = graph.get_state(graph_config)
            if snapshot.next:
                status = "paused"
            else:
                decision = snapshot.values.get("decision")
                if decision:
                    store.add_decision(run_id, decision)
                    print(f"\nVC decision: {decision['decision']} (conviction {decision['conviction']:.0%})")
                if not dry_run:
                    remember_run(deps.vc_memory, run_id, company.company_id, snapshot.values["claims"])
        except BudgetExceeded as e:
            status = "budget_exceeded"
            print(f"\nstopped: {e}", file=sys.stderr)
        except LLMError as e:
            status = "error"
            print(f"\nstopped: {e}", file=sys.stderr)
        except BaseException as e:  # API errors, Ctrl-C: record the failure, then let it surface
            store.add_event(run_id, "run_failed", {"error": f"{type(e).__name__}: {e}"})
            store.end_run(run_id, "error" if isinstance(e, Exception) else "interrupted", deps.spent_usd)
            raise
        finally:
            if isinstance(retriever, PgRetriever):
                retriever.conn.close()
    if status == "paused":
        store.pause_run(run_id, deps.spent_usd)
    else:
        store.end_run(run_id, status, deps.spent_usd)

    rows = store.turns(run_id)
    claims = store.conn.execute("SELECT verdict, COUNT(*) FROM claims WHERE run_id=? GROUP BY verdict", (run_id,)).fetchall()
    if claims:
        print("\nclaims: " + ", ".join(f"{n} {v}" for v, n in claims))
    stats = store.call_stats(run_id)
    share = stats["system1_calls"] / stats["calls"] if stats["calls"] else 0
    print(f"model calls: {stats['calls']} ({stats['system1_calls']} System-1, {share:.0%}); "
          f"{stats['input_tokens']} in / {stats['output_tokens']} out; ${stats['cost_usd']:.4f} "
          f"{'estimated' if dry_run else 'billed from usage'}")
    print(f"\n{status}: {len(rows)} turns, ${deps.spent_usd:.4f} total including checks")
    print(f"stored in {DATA / 'debate.sqlite'} (run {run_id}); checkpoints and memory in Postgres")
    return 0 if status in ("completed", "paused") else 1


def compare_cmd(args: argparse.Namespace) -> int:
    store = SqliteStore(DATA / "debate.sqlite")
    runs = []
    for run_id, expected in ((args.routed, "routed"), (args.baseline, "opus_low")):
        row = store.conn.execute("SELECT company_id, status, config_json, total_cost_usd FROM runs WHERE run_id=?",
                                 (run_id,)).fetchone()
        if row is None:
            print(f"error: unknown run {run_id}", file=sys.stderr)
            return 2
        company, status, config_json, cost = row
        config = json.loads(config_json)
        if config.get("profile") != expected or status != "completed":
            print(f"error: {run_id} must be a completed {expected} run", file=sys.stderr)
            return 2
        runs.append((run_id, company, config, cost, store.call_stats(run_id)))
    routed, baseline = runs
    if routed[1] != baseline[1] or routed[2]["limits"]["max_rounds"] != baseline[2]["limits"]["max_rounds"]:
        print("error: runs must have the same company and round limit", file=sys.stderr)
        return 2
    # Simulated dry-run costs vs billed usage, or RAG vs no-RAG prompts, would make the savings figure meaningless.
    mismatched = [k for k in ("dry_run", "with_rag", "no_rag") if routed[2].get(k) != baseline[2].get(k)]
    if mismatched:
        print(f"error: runs differ in {', '.join(mismatched)}; compare runs made the same way", file=sys.stderr)
        return 2
    estimated = routed[2].get("dry_run") or baseline[2].get("dry_run")
    print(f"{routed[1]} | {'simulated estimates' if estimated else 'recorded usage'}")
    for name, item in (("routed", routed), ("opus_low", baseline)):
        stats = item[4]
        share = stats["system1_calls"] / stats["calls"] if stats["calls"] else 0
        print(f"{name}: run {item[0]}, ${item[3]:.4f}, {stats['calls']} calls, {share:.0%} System-1")
    delta = baseline[3] - routed[3]
    pct = delta / baseline[3] if baseline[3] else 0
    print(f"routed savings: ${delta:.4f} ({pct:.1%} of baseline)")
    return 0


def eval_report_cmd(args: argparse.Namespace) -> int:
    from debate.eval import build_dashboard
    store = SqliteStore(DATA / "debate.sqlite")
    output = Path(args.output) if args.output else DATA / "evaluation_dashboard.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_dashboard(store, args.company))
    print(f"evaluation dashboard: {output.resolve()}")
    return 0


def eval_retrieval_cmd(args: argparse.Namespace) -> int:
    """Score retrieval on eval/retrieval_questions.yaml (hit@k, MRR). Local embeddings only: no model API calls."""
    from debate.rag import db
    from debate.rag.embeddings import FastEmbedder
    from debate.rag.evaluate import QUESTIONS, evaluate, load_questions, report_markdown
    from debate.rag.retriever import PgRetriever

    load_env()
    company, questions = load_questions(Path(args.questions) if args.questions else QUESTIONS)
    conn = db.connect()
    try:
        result = evaluate(PgRetriever(conn, FastEmbedder()), company, questions, k=args.k)
    finally:
        conn.close()
    output = Path(args.output) if args.output else DATA / "retrieval_eval.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_markdown(result))
    o = result["overall"]
    print(f"retrieval: {o['n']} questions, hit@{args.k} {o['hit_at_k']:.0%}, MRR {o['mrr']:.2f} -> {output.resolve()}")
    return 0


def eval_stability_cmd(args: argparse.Namespace) -> int:
    """Collect independent live repetitions for the decision-stability target."""
    if args.repeats < 1 or args.rounds < 1:
        print("error: --repeats and --rounds must be positive", file=sys.stderr)
        return 2
    for index in range(args.repeats):
        print(f"\nStability repetition {index + 1}/{args.repeats}")
        result = main(["run", "--company", args.company, "--rounds", str(args.rounds), "--profile", args.profile])
        if result != 0:
            return result
    return eval_report_cmd(argparse.Namespace(company=args.company, output=None))


def export_cmd(args: argparse.Namespace) -> int:
    """Write Markdown pages (one per run + index) for a static site; no model calls."""
    from debate.export import export_runs

    written = export_runs(SqliteStore(DATA / "debate.sqlite"), Path(args.out), args.run_ids or None,
                          include_dry_runs=args.include_dry_runs)
    print(f"wrote {len(written)} Markdown files to {Path(args.out).resolve()}")
    return 0


def reset_cmd(args: argparse.Namespace) -> int:
    """Delete stored debates (runs, reports, checkpoints, VC memory); the corpus is kept."""
    from debate.reset import local_debate_files, reset_local_debates

    reports = Path(args.reports)
    if not args.yes:
        print("would delete:", *local_debate_files(DATA, reports), sep="\n  ")
        print("  + all Postgres checkpoint threads and VC memory items")
        print(f"keeps the corpus. Re-run with --yes to delete.")
        return 0
    load_env()
    result = reset_local_debates(DATA, reports)
    print(f"deleted {len(result['files'])} files/folders, {result['checkpoint_threads']} checkpoint threads, "
          f"{result['memory_items']} memory items")
    return 0


def replay_cmd(args: argparse.Namespace) -> int:
    """Read a saved checkpoint without re-running any model or writing to memory."""
    load_env()
    saved = SqliteStore(DATA / "debate.sqlite").get_run(args.run_id)
    if not saved or saved["config"].get("checkpoint_backend") != "postgres":
        print("error: no Postgres checkpoints for that run", file=sys.stderr)
        return 2
    with persistence() as (saver, _):
        config = {"configurable": {"thread_id": saved["thread_id"]}}
        if args.list:
            for item in saver.list(config):
                values = item.checkpoint.get("channel_values", {})
                print(f"{item.config['configurable']['checkpoint_id']}  "
                      f"round={values.get('round', 0)} turns={len(values.get('turns', []))}")
            return 0
        if args.checkpoint_id:
            config["configurable"]["checkpoint_id"] = args.checkpoint_id
        item = saver.get_tuple(config)
        if item is None:
            print("error: checkpoint not found", file=sys.stderr)
            return 2
        values = item.checkpoint.get("channel_values", {})
        print(f"run {args.run_id} | checkpoint {item.config['configurable']['checkpoint_id']} "
              f"| round {values.get('round', 0)}")
        for turn in values.get("turns", []):
            print(f"[{turn['seq']}] {LABEL[turn['speaker']]}: {turn['content']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="debate")
    sub = p.add_subparsers(dest="cmd", required=True)
    bc = sub.add_parser("build-corpus", help="PDF -> dated topic files, exhibit tables, endnotes (no model calls)")
    bc.add_argument("--company", default="biobot")
    bc.set_defaults(fn=build_corpus_cmd)
    ing = sub.add_parser("ingest", help="validate and embed the built corpus into Postgres")
    ing.add_argument("--company", default="biobot")
    ing.set_defaults(fn=ingest_cmd)
    r = sub.add_parser("run", help="run one debate")
    r.add_argument("--company", default="biobot")
    r.add_argument("--rounds", type=int, help="override limits.max_rounds")
    r.add_argument("--dry-run", action="store_true", help="scripted replies, no API calls; skips RAG unless --with-rag")
    r.add_argument("--with-rag", action="store_true", help="retrieve real case chunks during an offline dry run")
    r.add_argument("--no-rag", action="store_true", help="run without retrieval (agents see no evidence)")
    r.add_argument("--profile", choices=("routed", "opus_low"), default="routed",
                   help="routed uses System-1; opus_low sends every call to Opus at low effort")
    r.add_argument("--pause-after", choices=("vc_open", "vc_question", "route", "ceo", "cfo", "cto", "devil", "fact_check"),
                   help="save a checkpoint and pause after this node (resume with debate resume RUN_ID)")
    r.set_defaults(fn=run)
    ex = sub.add_parser("export", help="write runs (transcripts, maths, fact-checks, decision, costs) to Markdown")
    ex.add_argument("run_ids", nargs="*", metavar="RUN_ID", help="runs to export (default: all live runs)")
    ex.add_argument("--out", default=str(ROOT / "reports"), help="output folder (default: reports/)")
    ex.add_argument("--include-dry-runs", action="store_true", help="also export scripted dry runs")
    ex.set_defaults(fn=export_cmd)
    rz = sub.add_parser("reset", help="delete stored debates (runs, reports, checkpoints, VC memory); keeps the corpus")
    rz.add_argument("--yes", action="store_true", help="actually delete (without it, only lists what would go)")
    rz.add_argument("--reports", default=str(ROOT / "reports"), help="exported Markdown folder to remove")
    rz.set_defaults(fn=reset_cmd)
    rs = sub.add_parser("resume", help="continue a paused or interrupted Postgres-checkpointed run")
    rs.add_argument("resume_run_id", metavar="RUN_ID")
    rs.set_defaults(fn=run)
    rp = sub.add_parser("replay", help="show turns from a saved checkpoint without model calls")
    rp.add_argument("run_id", metavar="RUN_ID")
    rp.add_argument("--list", action="store_true", help="list checkpoint ids")
    rp.add_argument("--checkpoint-id", help="show this checkpoint instead of the latest")
    rp.set_defaults(fn=replay_cmd)
    c = sub.add_parser("compare", help="compare a routed and an opus_low run (same company and round limit)")
    c.add_argument("--routed", required=True, help="run id from --profile routed")
    c.add_argument("--baseline", required=True, help="run id from --profile opus_low")
    c.set_defaults(fn=compare_cmd)
    er = sub.add_parser("eval-report", help="write the SQL-backed evaluation dashboard")
    er.add_argument("--company", default="biobot")
    er.add_argument("--output", help="Markdown report path (default: data/evaluation_dashboard.md)")
    er.set_defaults(fn=eval_report_cmd)
    erv = sub.add_parser("eval-retrieval", help="score retrieval on the labelled question set (hit@k, MRR)")
    erv.add_argument("--k", type=int, default=4)
    erv.add_argument("--questions", help="question set YAML (default: eval/retrieval_questions.yaml)")
    erv.add_argument("--output", help="Markdown report path (default: data/retrieval_eval.md)")
    erv.set_defaults(fn=eval_retrieval_cmd)
    es = sub.add_parser("eval-stability", help="collect independent live runs for decision-stability scoring")
    es.add_argument("--company", default="biobot")
    es.add_argument("--repeats", type=int, default=5)
    es.add_argument("--rounds", type=int, default=9)
    es.add_argument("--profile", choices=("routed", "opus_low"), default="routed")
    es.set_defaults(fn=eval_stability_cmd)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
