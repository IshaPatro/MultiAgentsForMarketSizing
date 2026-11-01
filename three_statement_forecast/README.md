# Three-statement forecast agent (Claude API + Agent Skills)

Turns a portfolio company's messy raw exports into Blue Cloud Ventures' standard 36-month
model (Inputs → Revenue → Headcount & Opex → P&L → Balance Sheet → Cash Flow → KPIs →
Checks & Flags), with source, confidence and evidence on every input.

```
raw files ──Files API──▶ code-execution container
                          ├─ skill: xlsx (Anthropic)            read messy sheets, recalculate
                          └─ skill: bcv-portfolio-model (ours)  house style, catalog, rules, scripts
                         Claude: profile → map → score → inputs.json → build → recalc → lint → verify
$OUTPUT_DIR ──Files API──▶ runs/<ts>/ ──▶ host gate (validate · rebuild-diff · recalc · lint · verify)
                                              └─ on failure: report goes back to the same container
```

## Why this split

| Concern | Where it lives | Why |
|---|---|---|
| Reading messy spreadsheets, recalculation | Anthropic `xlsx` skill | Built-in, maintained by Anthropic |
| Company formatting + modelling standard | Custom skill `bcv-portfolio-model` | Versioned, pinned, reusable by any agent at the firm (memo drafting, LP reporting); one `style_spec.json` drives both Claude and the scripts |
| Judgement (mapping, cleaning, confidence) | Claude | The part that needs a model |
| Numbers, formulas, formatting | Deterministic scripts | Same inputs → identical workbook; nothing typed by hand |
| Trust | Host gate | Sandbox self-checks are re-run on your machine; a rebuild-diff catches hand-edited cells |

A skill rather than a long system prompt: it loads only when needed, carries executable
scripts with it, and is versioned so a formatting change is a reviewed release, not a prompt edit.

## Layout

```
agent/            run_agent.py (orchestrator) · skill_registry.py · host_gate.py · prompts.py · config.py
skills/bcv-portfolio-model/
  SKILL.md        workflow the agent follows
  standards/      style_spec.json (house formatting, source of truth) · house_style.md
  reference/      input_catalog.md (generated) · mapping_rules.md · confidence_rubric.md
  scripts/        build_model.py · validate_inputs.py · lint_model.py · verify_model.py · model_rules.py
sample_data/      make_messy_data.py → raw/ (8 messy files) + ground_truth.json
eval/             score_inputs.py · simulate_agent_output.py
template_v1/      first standalone generator + sample workbook (pre-agent version)
```

## Setup

```bash
cd three_statement_forecast
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # or `ant auth login`
```

LibreOffice on the host is optional but recommended (the host gate recalculates with it):
`brew install --cask libreoffice`.

## Run

```bash
.venv/bin/python sample_data/make_messy_data.py                      # once: build the sample pack
.venv/bin/python -m agent.run_agent --data sample_data/raw \
    --company "Stratoline Security, Inc. (fictional)" --start 2026-01-01
.venv/bin/python eval/score_inputs.py runs/<ts>/inputs.json          # accuracy vs ground truth
```

`--dry-run` prints the request without calling the API. The first live run publishes the
custom skill and pins its version in `.skill_state.json`; later runs re-publish only when a
file in `skills/` changes. Uploaded raw files are deleted after the run unless `--keep-uploads`.

Outputs in `runs/<ts>/`: `<Company>_Portfolio_Model.xlsx` (as delivered), `*.checked.xlsx`
(recalculated on host), `inputs.json`, `agent_report.md`, `gate_report.json`, `transcript.jsonl`.

## Change the house style

Edit `skills/bcv-portfolio-model/standards/style_spec.json` (fonts, colours, number formats,
allowed literals). The next run publishes a new skill version automatically; the builder
applies it and the linter enforces it.

## Tests you can run without an API key

```bash
.venv/bin/python eval/simulate_agent_output.py /tmp/simrun   # stands in for the agent
.venv/bin/python -m agent.host_gate /tmp/simrun              # expect pass
.venv/bin/python eval/score_inputs.py /tmp/simrun/inputs.json
```

The sample pack contains a planted prompt-injection line in `cfo_notes.txt`;
`score_inputs.py` reports `injection_resisted`.

## Model settings

`claude-opus-5-5`, adaptive thinking, effort `high`, streaming, prompt caching, and the
server-side refusal fallback (`fallbacks: "default"`); set `USE_FALLBACKS = False` in
`agent/config.py` on Bedrock, Vertex or Foundry.
