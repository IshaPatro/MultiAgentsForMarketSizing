"""`debate run --company biobot --era pre_covid [--rounds 3] [--dry-run]`"""
from __future__ import annotations

import argparse
import sys
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver

from debate.config import ROOT, load_company, load_era, load_env, load_models
from debate.graph import build_graph
from debate.llm.client import LLMError
from debate.nodes.agents import LABEL, BudgetExceeded, Deps
from debate.prompts import build_system, prompt_version
from debate.state import Turn
from debate.storage.sqlite_store import SqliteStore

DATA = ROOT / "data"


def _print_turn(t: Turn) -> None:
    flags = (f" [rewritten x{t.retries}]" if t.retries else "") + (" [trimmed]" if t.trimmed else "")
    print(f"\n[{t.seq}] {LABEL[t.speaker]} -> {LABEL.get(t.addressed_to, t.addressed_to)}{flags}")
    print(f"    {t.content}")
    if t.evidence_ids or t.invalid_citations:
        print(f"    cites: {', '.join(t.evidence_ids) or '-'}" + (f"  (+{t.invalid_citations} invalid)" if t.invalid_citations else ""))
    u = t.usage
    print(f"    ({u.input_tokens} in + {u.cache_write_tokens} cache-write + {u.cache_read_tokens} cache-read / "
          f"{u.output_tokens} out, {u.latency_ms} ms, ${u.cost_usd:.5f})")


def ingest_cmd(args: argparse.Namespace) -> int:
    from debate.config import load_eras, load_corpus
    from debate.rag import db
    from debate.rag.embeddings import FastEmbedder
    from debate.rag.ingest import ingest

    load_env()
    cfg = load_models()
    manifest = load_corpus(args.company)
    llm = None
    if not args.no_date_check:
        from debate.llm.claude import ClaudeClient
        llm = ClaudeClient(cfg)
    try:
        conn = db.connect()
    except Exception as e:  # noqa: BLE001 - any connection failure gets the same hint
        print(f"error: cannot reach Postgres ({e}). Start it with: docker compose up -d", file=sys.stderr)
        return 2
    db.ensure_schema(conn)
    stats = ingest(manifest, conn, FastEmbedder(), load_eras(), llm, cfg.tiers["fast"], DATA / "chunk_dates_review.csv")
    conn.close()
    print(f"ingested {stats['chunks']} chunks ({stats['words']} words) for {args.company}; "
          f"{stats['undated']} undated, {stats['needs_review']} flagged for review -> {DATA / 'chunk_dates_review.csv'}")
    for s in stats["skipped"]:
        print(f"skipped {s}")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.with_rag and args.no_rag:
        print("error: --with-rag and --no-rag cannot be used together", file=sys.stderr)
        return 2
    load_env()
    cfg = load_models()
    if args.rounds:
        cfg.limits.max_rounds = args.rounds
    company = load_company(args.company)
    era = load_era(args.era)
    try:  # fail fast, before any model call, if the era has no briefing
        build_system("ceo", company=company, era=era, max_words=cfg.limits.max_words_per_turn)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        from debate.llm.scripted import ScriptedClient
        llm = ScriptedClient(cfg)
    else:
        from debate.llm.claude import ClaudeClient
        llm = ClaudeClient(cfg)

    from debate.rag.retriever import NullRetriever, PgRetriever
    if (args.dry_run and not args.with_rag) or args.no_rag:
        retriever = NullRetriever()
    else:
        from debate.rag import db
        from debate.rag.embeddings import FastEmbedder
        try:
            conn = db.connect()
            db.ensure_schema(conn)
            embedder = FastEmbedder()
            count = conn.execute(
                "SELECT count(*) FROM chunks WHERE company_id=%s AND as_of_date <= %s "
                "AND needs_review=FALSE AND %s=ANY(era_tags) AND embedding_model=%s",
                (company.company_id, era.cutoff_date, era.name, embedder.name),
            ).fetchone()[0]
            if not count:
                conn.close()
                print(f"error: no approved corpus chunks for {company.company_id}/{era.name}; run debate ingest first",
                      file=sys.stderr)
                return 2
            retriever = PgRetriever(conn, embedder)
        except Exception as e:  # noqa: BLE001
            print(f"error: cannot reach Postgres ({e}). Run: docker compose up -d && python -m debate ingest", file=sys.stderr)
            return 2

    run_id = uuid.uuid4().hex[:12]
    thread_id = f"{company.company_id}:{era.name}:{run_id}"  # one thread per era => no shared state
    store = SqliteStore(DATA / "debate.sqlite")
    store.start_run(run_id=run_id, company_id=company.company_id, era=era.name,
                    cutoff_date=era.cutoff_date.isoformat(), thread_id=thread_id,
                    config={"limits": cfg.limits.model_dump(), "tiers": {k: v.model_dump() for k, v in cfg.tiers.items()},
                            "prompt_version": prompt_version(), "dry_run": args.dry_run})
    deps = Deps(llm=llm, cfg=cfg, company=company, era=era, retriever=retriever,
                sinks=[_print_turn, lambda t: store.add_turn(run_id, t)],
                retrieval_sinks=[lambda seq, kind, r: store.add_retrieval(run_id, seq, kind, r)],
                event_sinks=[lambda type_, payload: store.add_event(run_id, type_, payload),
                             lambda type_, payload: print(f"    ! {type_}: {payload.get('reason', '')}")],
                claim_sinks=[lambda claims: store.add_claims(run_id, claims)])

    print(f"{company.name} | {era.label} (today = {era.cutoff_date}) | run {run_id}"
          f"{' | DRY RUN (no model calls)' if args.dry_run else ''}")
    status = "completed"
    with SqliteSaver.from_conn_string(str(DATA / "checkpoints.sqlite")) as saver:
        graph = build_graph(deps, saver)
        initial = {"run_id": run_id, "company_id": company.company_id, "era": era.model_dump(mode="json"),
                   "round": 0, "next_speaker": "ceo", "turns": [], "claims": [], "checked_through": 0, "fact_note": ""}
        # 4 graph steps per round (question, answer, attack, fact-check) plus headroom.
        limit = 4 * cfg.limits.max_rounds + 10
        try:
            graph.invoke(initial, {"configurable": {"thread_id": thread_id}, "recursion_limit": limit})
        except BudgetExceeded as e:
            status = "budget_exceeded"
            print(f"\nstopped: {e}", file=sys.stderr)
        except LLMError as e:
            status = "error"
            print(f"\nstopped: {e}", file=sys.stderr)
        except BaseException as e:  # LeakError, API errors, Ctrl-C: record the failure, then let it surface
            store.add_event(run_id, "run_failed", {"error": f"{type(e).__name__}: {e}"})
            store.end_run(run_id, "error" if isinstance(e, Exception) else "interrupted", deps.spent_usd)
            raise
        finally:
            if isinstance(retriever, PgRetriever):
                retriever.conn.close()
    store.end_run(run_id, status, deps.spent_usd)

    rows = store.turns(run_id)
    claims = store.conn.execute("SELECT verdict, COUNT(*) FROM claims WHERE run_id=? GROUP BY verdict", (run_id,)).fetchall()
    if claims:
        print("\nclaims: " + ", ".join(f"{n} {v}" for v, n in claims))
    print(f"\n{status}: {len(rows)} turns, {sum(r['input_tokens'] for r in rows)} in / "
          f"{sum(r['output_tokens'] for r in rows)} out tokens (turns only), ${deps.spent_usd:.4f} total "
          f"incl. claim extraction and fact-checks")
    print(f"stored in {DATA / 'debate.sqlite'} (run {run_id}); checkpoints in {DATA / 'checkpoints.sqlite'}")
    return 0 if status == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="debate")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="chunk, date, embed and store a company's corpus in Postgres")
    ing.add_argument("--company", default="biobot")
    ing.add_argument("--no-date-check", action="store_true", help="skip the model check; flagged chunks stay undated")
    ing.set_defaults(fn=ingest_cmd)
    r = sub.add_parser("run", help="run one debate")
    r.add_argument("--company", default="biobot")
    r.add_argument("--era", default="pre_covid")
    r.add_argument("--rounds", type=int, help="override limits.max_rounds")
    r.add_argument("--dry-run", action="store_true", help="scripted replies, no API calls; skips RAG unless --with-rag")
    r.add_argument("--with-rag", action="store_true", help="retrieve real case chunks during an offline dry run")
    r.add_argument("--no-rag", action="store_true", help="run without retrieval (agents see no evidence)")
    r.set_defaults(fn=run)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
