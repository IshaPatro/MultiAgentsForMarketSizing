# MultiAgentsForMarketSizing

Multi-agent VC debate (CEO / CFO / CTO vs. a Devil's Advocate, questioned by a VC) on LangGraph + Claude.
Architecture and design rationale: `implementation.md` (git-ignored, local only).

## Status
Phases 0–3 are built: the debate runs on LangGraph with SQLite checkpoints, agents retrieve dated Biobot case chunks from Postgres/pgvector, and a neutral fact-checker checks claims from every speaking role. System-1 routing, persistent Postgres memory, and the VC decision come in later phases.

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m debate run --dry-run          # offline, scripted replies, no API key
export ANTHROPIC_API_KEY=...                       # or: ant auth login
.venv/bin/python -m debate run --era pre_covid     # real run (3 rounds, Sonnet 5.5)
.venv/bin/python -m pytest
```
Transcripts and per-turn token/cost metadata land in `data/debate.sqlite`; LangGraph checkpoints in `data/checkpoints.sqlite`.

## Phase 2 corpus

The licensed case PDF must be present at `Biobot/01.BiobotAnalytics.pdf` and stays out of Git. Start Postgres and ingest it before a real debate:

```bash
docker compose up -d
.venv/bin/python -m debate ingest --company biobot --no-date-check
.venv/bin/python -m debate run --company biobot --era pre_covid
.venv/bin/python -m debate run --dry-run --with-rag --rounds 1  # offline end-to-end citation check
```

Ingestion cleans the PDF, keeps exhibits intact, embeds chunks locally, and writes them to `chunks`. A chunk without an approved date is withheld from retrieval. `configs/corpus/biobot.yaml` records reviewed dates for the four chunks that mention future years as forecasts or fiscal-year labels. New ambiguous chunks appear in `data/chunk_dates_review.csv`; inspect the PDF and add the reviewed date with the chunk's `text_sha256` from that CSV to the manifest before using it as evidence. An approval whose fingerprint no longer matches (e.g. after a chunking change) is ignored and the chunk returns to review. Omit `--no-date-check` to let the configured model propose dates, but proposed ambiguous dates still require review.

The retriever filters by company, era, cutoff date, review status, and embedding model in SQL. Agent citations are accepted only when their chunk IDs were actually retrieved. `--dry-run` uses scripted replies and skips RAG unless `--with-rag` is supplied; `--no-rag` skips RAG for a real-model run.

## Phase 3 fact-check and firewall

Every turn, including VC questions, is scanned for post-cutoff knowledge. A blocked turn is regenerated up to twice; if it still fails, the transcript stores only a withholding marker and the VC never sees the rejected text. The fact-checker extracts atomic claims from all roles, checks factual and numeric claims against era-filtered evidence, escalates uncertain or contradictory verdicts, and records verdicts and citations in SQLite. Explicit arithmetic is recomputed in Python. Claims with no eligible evidence or no valid supporting citation are marked unverifiable. The pre-COVID leakage tests plant hindsight in both company and VC turns and verify it never reaches later prompts.
