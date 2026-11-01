"""Run the portfolio-model agent end to end.

  raw files ──Files API──▶ container (skills: Anthropic xlsx + BCV house standard)
        Claude profiles, maps, scores confidence, builds and self-checks
  $OUTPUT_DIR ──Files API──▶ runs/<timestamp>/  ──▶ host gate (validate, rebuild-diff, recalc, lint, verify)
        gate failures go back to the same container for a fix (up to MAX_GATE_ROUNDS)

Usage:
  python -m agent.run_agent --company "Acme, Inc." --data sample_data/raw
  python -m agent.run_agent --data sample_data/raw --dry-run     # print the request, no API calls
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from . import config, host_gate, prompts, skill_registry


def request_params(container, messages):
    params = dict(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        system=prompts.SYSTEM,
        messages=messages,
        tools=[config.CODE_EXECUTION_TOOL],
        container=container,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": config.EFFORT},
        cache_control={"type": "ephemeral"},
    )
    if config.USE_FALLBACKS:
        params.update(betas=[config.FALLBACK_BETA], fallbacks="default")
    return params


def file_ids(message):
    """File ids captured from $OUTPUT_DIR in this response's code-execution results."""
    ids = []
    for block in message.content:
        if block.type == "bash_code_execution_tool_result" and block.content.type == "bash_code_execution_result":
            ids += [out.file_id for out in block.content.content if getattr(out, "file_id", None)]
    return ids


def run_round(client, container, messages, log):
    """One agent round: stream, resume on pause_turn, return (final message, container dict, file ids)."""
    produced = []
    for _ in range(config.MAX_PAUSE_CONTINUATIONS + 1):
        with client.beta.messages.stream(**request_params(container, messages)) as stream:
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                    print("  · running code in container", flush=True)
            msg = stream.get_final_message()
        log.write(msg.to_json(indent=None) + "\n")
        log.flush()
        for block in msg.content:
            if block.type == "thinking" and block.thinking:
                print(f"  ~ {block.thinking.strip()[:160]}", flush=True)
        produced += file_ids(msg)
        container = {**container, "id": msg.container.id} if getattr(msg, "container", None) else container
        messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason == "pause_turn":
            continue
        if msg.stop_reason == "max_tokens":
            messages.append({"role": "user", "content": "Continue from where you stopped."})
            continue
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"Request declined: {getattr(msg.stop_details, 'category', None)}")
        return msg, container, produced
    raise RuntimeError("Agent did not finish within MAX_PAUSE_CONTINUATIONS")


def download(client, ids, run_dir):
    saved = {}
    for fid in ids:  # later deliveries of the same name overwrite earlier ones
        meta = client.files.retrieve_metadata(fid)
        path = run_dir / Path(meta.filename).name
        client.files.download(fid).write_to_file(path)
        saved[path.name] = fid
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="folder of raw files (csv, xlsx, md, txt, pdf)")
    ap.add_argument("--company", default="the portfolio company")
    ap.add_argument("--start", default="2026-01-01", help="first forecast month (YYYY-MM-DD)")
    ap.add_argument("--notes", default="", help="deal-team context passed to the agent")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-uploads", action="store_true", help="don't delete uploaded raw files afterwards")
    args = ap.parse_args()

    raw = sorted(p for p in Path(args.data).iterdir() if p.is_file() and not p.name.startswith("."))
    if not raw:
        sys.exit(f"No files in {args.data}")
    text = prompts.task(args.company, args.start, [p.name for p in raw], args.notes)

    if args.dry_run:
        container = {"skills": config.ANTHROPIC_SKILLS + [{"type": "custom", "skill_id": "<published on first run>",
                                                           "version": "<pinned>"}]}
        content = [{"type": "text", "text": text}] + [{"type": "container_upload", "file_id": f"<{p.name}>"}
                                                       for p in raw]
        print(json.dumps({k: v for k, v in request_params(container, [{"role": "user", "content": content}]).items()
                          if k != "system"}, indent=2, default=str))
        return

    client = anthropic.Anthropic()
    run_dir = config.RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    container = {"skills": config.ANTHROPIC_SKILLS + [skill_registry.ensure_custom_skill(client)]}

    uploads = [client.files.upload(file=p) for p in raw]
    content = [{"type": "text", "text": text}] + [{"type": "container_upload", "file_id": u.id} for u in uploads]
    messages = [{"role": "user", "content": content}]

    try:
        with open(run_dir / "transcript.jsonl", "w") as log:
            for round_no in range(config.MAX_GATE_ROUNDS + 1):
                print(f"Round {round_no + 1}: agent working…", flush=True)
                msg, container, ids = run_round(client, container, messages, log)
                saved = download(client, ids, run_dir)
                print(f"  downloaded: {', '.join(saved) or 'nothing'}")
                report = host_gate.gate(run_dir)
                print(f"  host gate: {report['status']}")
                if report["status"] == "pass" or round_no == config.MAX_GATE_ROUNDS:
                    break
                messages.append({"role": "user", "content": prompts.gate_feedback(json.dumps(report, indent=2))})
        summary = "\n".join(b.text for b in msg.content if b.type == "text")
        (run_dir / "agent_summary.txt").write_text(summary)
        print(f"\n{summary}\n\nRun folder: {run_dir}\nHost gate: {report['status']}")
    finally:
        if not args.keep_uploads:
            for u in uploads:
                client.files.delete(u.id)


if __name__ == "__main__":
    main()
