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
    u = t.usage
    print(f"    ({u.input_tokens} in + {u.cache_write_tokens} cache-write + {u.cache_read_tokens} cache-read / "
          f"{u.output_tokens} out, {u.latency_ms} ms, ${u.cost_usd:.5f})")


def run(args: argparse.Namespace) -> int:
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

    run_id = uuid.uuid4().hex[:12]
    thread_id = f"{company.company_id}:{era.name}:{run_id}"  # one thread per era => no shared state
    store = SqliteStore(DATA / "debate.sqlite")
    store.start_run(run_id=run_id, company_id=company.company_id, era=era.name,
                    cutoff_date=era.cutoff_date.isoformat(), thread_id=thread_id,
                    config={"limits": cfg.limits.model_dump(), "tiers": {k: v.model_dump() for k, v in cfg.tiers.items()},
                            "prompt_version": prompt_version(), "dry_run": args.dry_run})
    deps = Deps(llm=llm, cfg=cfg, company=company, era=era,
                sinks=[_print_turn, lambda t: store.add_turn(run_id, t)])

    print(f"{company.name} | {era.label} (today = {era.cutoff_date}) | run {run_id}"
          f"{' | DRY RUN (no model calls)' if args.dry_run else ''}")
    status = "completed"
    with SqliteSaver.from_conn_string(str(DATA / "checkpoints.sqlite")) as saver:
        graph = build_graph(deps, saver)
        initial = {"run_id": run_id, "company_id": company.company_id, "era": era.model_dump(mode="json"),
                   "round": 0, "next_speaker": "ceo", "turns": []}
        try:
            graph.invoke(initial, {"configurable": {"thread_id": thread_id}, "recursion_limit": 60})
        except BudgetExceeded as e:
            status = "budget_exceeded"
            print(f"\nstopped: {e}", file=sys.stderr)
        except LLMError as e:
            status = "error"
            print(f"\nstopped: {e}", file=sys.stderr)
    store.end_run(run_id, status)

    rows = store.turns(run_id)
    print(f"\n{status}: {len(rows)} turns, {sum(r['input_tokens'] for r in rows)} in / "
          f"{sum(r['output_tokens'] for r in rows)} out tokens, ${sum(r['cost_usd'] for r in rows):.4f}")
    print(f"stored in {DATA / 'debate.sqlite'} (run {run_id}); checkpoints in {DATA / 'checkpoints.sqlite'}")
    return 0 if status == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="debate")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one debate")
    r.add_argument("--company", default="biobot")
    r.add_argument("--era", default="pre_covid")
    r.add_argument("--rounds", type=int, help="override limits.max_rounds")
    r.add_argument("--dry-run", action="store_true", help="scripted replies, no API calls")
    r.set_defaults(fn=run)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
