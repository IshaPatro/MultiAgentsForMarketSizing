# MultiAgentsForMarketSizing

Multi-agent VC debate (CEO / CFO / CTO vs. a Devil's Advocate, questioned by a VC) on LangGraph + Claude.
Architecture and design rationale: `implementation.md` (git-ignored, local only).

## Status
Phase 0 (skeleton, configs, Docker/Postgres schema) and Phase 1 (5-agent debate on LangGraph, SQLite checkpointer + transcript store) are built. RAG, fact-checking, System-1 routing and the VC decision come in later phases.

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m debate run --dry-run          # offline, scripted replies, no API key
export ANTHROPIC_API_KEY=...                       # or: ant auth login
.venv/bin/python -m debate run --era pre_covid     # real run (3 rounds, Sonnet 5.5)
.venv/bin/python -m pytest
```
Transcripts and per-turn token/cost metadata land in `data/debate.sqlite`; LangGraph checkpoints in `data/checkpoints.sqlite`.

`docker compose up -d` starts Postgres + pgvector (not needed until Phase 2).
