# NextGen Boardroom

**Where bold ideas meet hard questions.**

Multi-agent VC debate (CEO / CFO / CTO vs. a Devil's Advocate, questioned by a VC) on LangGraph + Claude.
Architecture and design rationale: `implementation.md` (git-ignored, local only).

## Status
The debate runs on LangGraph with Postgres checkpoints and agent-scoped long-term memory. Agents retrieve and cite Biobot case evidence from pgvector, a neutral fact-checker checks claims from every speaking role, arithmetic is re-checked in code, and a final VC decision is saved for each completed run. Model calls are routed and measured. Run `eval-report` to see which quality targets have passed, failed or still need evidence.

## Run
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m debate run --dry-run          # offline, scripted replies, no API key
export ANTHROPIC_API_KEY=...                       # or: ant auth login
.venv/bin/python -m debate run                     # real run (9 rounds; Sonnet 5.5 debate, Haiku 5.5 checks, Opus 5.5 decision)
.venv/bin/python -m pytest
```
Transcripts and per-turn token/cost metadata land in `data/debate.sqlite`; LangGraph checkpoints and long-term memory live in Postgres.

## Corpus

The corpus is the only evidence agents can cite; nothing searches the web at run time. Biobot source files live under `case_study/biobot/`, including the licensed PDF and public research ledger. The local manifest, generated files, and curated files live under `corpus/biobot/`. Both parent folders are git-ignored because the corpus and manifest contain case-derived material. **Every chunk is dated** (`as_of`: the date the information refers to).

```bash
docker compose up -d
.venv/bin/python -m debate build-corpus     # PDF -> corpus/biobot/ (no model calls)
.venv/bin/python -m debate ingest           # validate + embed locally into pgvector
.venv/bin/python -m debate eval-retrieval   # hit@4 / MRR on eval/retrieval_questions.yaml
.venv/bin/python -m debate run --dry-run --with-rag --rounds 1  # offline end-to-end citation check
```

| `corpus/biobot/` | What | Built by |
|---|---|---|
| `narrative/NN_<topic>.md` | Case paragraphs grouped by topic (founders, origin, science, technology & IP, market, customers & GTM, competition, risks, financials, decision); page-split paragraphs re-joined; endnote markers resolved into links | `build-corpus` |
| `exhibits/ex5.csv`, `ex6.csv` | Market-segmentation matrices, one row per segment | `build-corpus` |
| `exhibits/ex7.csv` | Boston FY16–FY18 public-health budget, one row per line item, reconciled against every printed total | `build-corpus` |
| `sources/endnotes.yaml` | The case's 44 endnotes, each with its own publication date and the paragraphs that cite it | `build-corpus` |
| `sources/public.yaml` | The public ledger as 98 dated items (one per table row, bullet or paragraph), topic-tagged via the local `corpus/biobot/manifest.yaml`, links kept aside, each with the rule used to date it; screenshot-only funding rows become `public_lead` chunks marked UNVERIFIED | `build-corpus` |
| `facts.yaml` | ~90 atomic facts: statement, value/unit, date, page, verbatim quote, endnote, kind (reported, estimate, claim, forecast, plan, quote, derived) and who said it | curated |
| `assumptions.yaml` | Sizing inputs that are **not** in the case (world population, price per sample, capture rates), labelled as assumptions; some cite ledger items as evidence (catchment size from the 2018 Cary pilot, the $15,000/yr municipal price floor) | curated |
| `sizing.yaml` | The slide's three TAM methods plus a price-floor cross-check, recomputed from facts and assumptions with the source of every input | curated |
| `README.md` | Index, Exhibit 7 reconciliation and sizing lineage | `build-corpus` |

Validation runs on every build and ingest: each fact's quote must appear on its PDF page, derived facts must recompute, endnote references must exist, topic overrides must match exactly one paragraph, and every chunk needs a valid date. Exhibit 7's reconciliation found two errors in the source exhibit: FY16 Emergency Medical Services is printed as "49,63,310" (the column total implies $49,693,309), and the FY16 Public Health Service Centers subtotal is $20K below its line items. The sizing lineage reproduces the slide's three TAMs ($511M, $744M, $6.51B) and shows which inputs are case facts, founder estimates or claims, derived figures, public-source values or assumptions. Ledger evidence must resolve to exactly one item with a matching date, so editing the ledger can't silently break a citation. The slide's 10,000 people per catchment now cites Cary's 2018 pilot (~4,000–15,000 people per site). The $1,000 per sample is cross-checked against the reported $15,000/yr municipal minimum. A fourth method, 870 cities × $15,000 = $13.05M, is stored with its caveat: it is a price floor, not a TAM.

Retrieval is hybrid: pgvector (local `bge-small` embeddings) plus in-process Okapi BM25, merged with Reciprocal Rank Fusion (k=5 over 40 candidates per list), with a topic header on every indexed chunk. On the case corpus (41 questions), hit@4 rose from 85% to 93% (MRR 0.74 to 0.79) when Postgres `ts_rank` was replaced by BM25 and headers were added. With the public ledger (364 chunks, 49 questions) it is 96% (MRR 0.82). Agents see at most one assumption, one sizing model and one unverified lead per answer, so slide inputs cannot crowd out case facts, and each VC agenda topic reserves half the evidence slots for its corpus topics. The fact-checker never accepts an assumption, sizing model or unverified lead as evidence. Agent citations are accepted only when their chunk IDs were actually retrieved. `--dry-run` uses scripted replies and skips RAG unless `--with-rag` is supplied; `--no-rag` skips RAG for a real-model run.

The curated files live under the git-ignored `corpus/biobot/` because they quote the licensed case. Back up the ignored `case_study/` and `corpus/` folders separately.

## Fact-check

The VC's assistant (the fact-checker) extracts atomic claims from every turn (including VC questions, and the maths and assumptions behind each argument). Calculations, including ones written in words ("870 cities multiplied by 30% gives 261"), are recomputed in Python and never sent to a model. Other factual claims go to JEV first: is the needed information in the retrieved evidence, and is the claim supported or contradicted? Only low-confidence or contradicted verdicts go to a stronger model. After each exchange the assistant posts one line in the conversation, "Facts are correct." or "Facts are incorrect." (incorrect = a contradicted or miscalculated claim); the detail goes to the VC privately and is stored in SQLite. Claims with no eligible evidence or no valid supporting citation are marked unverifiable.

## Model routing and cost comparison

System 1 is [JEV](https://api.typesafe.ai/v1/systemone) (TypeSafe's System One API), a classifier that answers typed questions with calibrated probabilities. It cannot write text, so it takes the classification jobs, starting with the **first-pass fact-check verdict**: each claim is one request (a `choice` question for supported / contradicted / unverifiable plus a yes/no question per evidence chunk, which decides what gets cited). Claim extraction needs generation and stays on Claude Haiku; debate turns and hard-check escalations use Sonnet; the final decision uses Opus. JEV also acts as the **moderator**: after the VC writes a question, JEV picks which executive answers and the conversation shows "This question will be taken up by the CFO." (follow-ups stay with the same executive). JEV is used on routed runs whenever `JEV_API_KEY` is set (optional `JEV_MODEL`, default `jev-latest`; optional `JEV_BASE_URL`, default `https://api.typesafe.ai/v1/systemone`). Without a key the verdict tier falls back to Haiku. JEV is billed $0.042 per million input tokens (output is free) and is recorded at that price in `model_calls`.

Every model call is stored in the `model_calls` table with role, tier, model, token counts, latency and cost. Run matching profiles and compare their recorded totals:

```bash
.venv/bin/python -m debate run --profile routed
.venv/bin/python -m debate run --profile opus_low
.venv/bin/python -m debate compare --routed ROUTED_RUN_ID --baseline OPUS_RUN_ID
```

`opus_low` sends all model calls to Opus at low effort for a cost baseline. Use the same company and round limit in both runs; `compare` refuses runs made differently (dry run vs live, RAG vs no RAG). Offline `--dry-run` runs can exercise the comparison, but their costs are simulated estimates.

## Checkpoints and memory

Postgres is required even for an offline debate because graph checkpoints are durable. To pause after a graph node and continue later:

```bash
.venv/bin/python -m debate run --dry-run --rounds 3 --pause-after ceo
.venv/bin/python -m debate resume RUN_ID
.venv/bin/python -m debate replay RUN_ID --list
.venv/bin/python -m debate replay RUN_ID --checkpoint-id CHECKPOINT_ID
```

`resume` also accepts an interrupted run left with status `running` after a process crash; it continues from the last saved node. A model call inside the interrupted node may be repeated and billed again. Replay reads saved turns without model calls. A completed real run writes a short note based only on supported claims to the VC's memory namespace. The VC recalls notes about other companies, never earlier debates about the company being pitched, so repeated runs stay independent. Offline runs never write long-term memory. `MemoryView` rejects reads or writes to another agent's namespace and logs the attempt.

## VC decision and evaluation

Each completed run saves an Opus VC decision with rubric scores, reasons, conditions and the supported claim IDs it relies on. The SQL views in `db/003_eval_views.sql` measure turn length, citation validity, miscalculations, memory access attempts, decisions and cost from saved runs. The dashboard grades only completed live runs using the current prompt version and reports unmet targets as `FAIL` or `PENDING`.

```bash
.venv/bin/python -m debate eval-report                 # data/evaluation_dashboard.md
.venv/bin/python -m debate eval-stability --repeats 5  # 5 independent live runs, then the dashboard
```

Decision stability needs five comparable independent live runs; the dashboard marks it pending until then. Grounding counts every extracted factual or numeric claim, including unverifiable ones, so it cannot be improved by merely validating already supplied citation IDs.

## Maths and assumptions

Every argument (CEO/CFO/CTO/Devil) carries a `calculation` (step-by-step `expression = result` with units) and up to three `assumptions` the evidence does not state, besides the 60-word point. Both are shown to the other agents and the VC, fact-checked like the point, and stored per turn. The arithmetic is re-checked in Python (`debate.numeric.check_calculation`: chained steps, units, rounding tolerance; ranges and rates are never guessed). A wrong calculation becomes a code-verified `miscalculated` claim that the VC sees.

## VC agenda and follow-ups

A debate runs `limits.max_rounds: 9` (pitch + four agenda topics × main question and follow-up). The VC's agenda (`src/debate/agenda.py`) opens with **market size**, addressed to the CFO, who may answer with up to three independent sizing methods (`estimates`: method, step-by-step maths, assumptions), each re-checked in code. Every main question is followed by a forced follow-up to the same executive on how they reached the answer: where each input comes from, which assumption drives it, why estimates differ and which belongs in a seed deck. The VC then picks the next uncovered topic (customers, unit economics, technology, go-to-market, risks).

## Notebook and Markdown export

`main.ipynb` (kernel "Python (MultiAgentsForMarketSizing .venv)") runs ingest, one debate, an optional cost baseline, the evaluation dashboard and the export. `debate export --out reports` writes one Markdown page per run (YAML front matter, transcript grouped by agenda topic with maths, assumptions, estimates and cited sources, fact-checks, decision, model calls and costs, audit) plus `index.md`; source chunk text is never copied. `debate reset --yes` deletes stored debates (runs, reports, checkpoints, VC memory) and keeps the corpus. Register the kernel on a new machine with `.venv/bin/python -m ipykernel install --user --name multiagents-debate`.

## Static debate pages

The public site is built from `web/`: `index.html` introduces the experiment, `biobot.html` compares optimized and Opus-only conversations, and `decision-memo.html` shows a separate memo for each run. The committed conversation and memo text is illustrative. The cost, token, and runtime figures come from two measured private runs. GitHub Pages regenerates the public pages from `site/public_demo.json` before each deployment.

To preview both **full private runs** locally, run `python3 scripts/build_report_page.py`. It embeds the newest completed `routed` and `opus_low` run for each company in the conversation and memo pages. Optional Markdown reports are generated with `.venv/bin/python -m debate export` and appear as links in local memos.

The private pages contain dialogue derived from the licensed case. Keep those local. Restore the publishable pages before committing with `python3 scripts/build_report_page.py --public-demo`. The deployment workflow performs this regeneration again in CI.
