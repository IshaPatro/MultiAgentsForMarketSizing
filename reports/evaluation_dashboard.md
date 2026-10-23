# Biobot evaluation dashboard

Prompt version `e58ca84a`: 2 completed live runs; 0 runs with other prompt versions excluded. Generated from saved SQLite run, turn, claim, retrieval, event, decision and model-call records.

## Targets

| Metric | Observed | Target | Status |
|---|---:|---:|---|
| Cross-agent memory access attempts | 0 | 0 | PASS |
| Factual claims with valid retrieved citations | 85.5% (1135/1328) | ≥95% | FAIL |
| Calculations that do not add up | 14 | 0 | FAIL |
| Decision stability | <5 comparable runs | ≥4/5 | PENDING |
| Recorded model cost | $17.5117 across 2 runs | measure | MEASURED |

## Live runs

| Run | Decision | Turns | Factual citation rate | Cost |
|---|---|---:|---:|---:|
| `1f3fc5752d73` | pass | 141 | 90.6% | $13.1932 |
| `4a2867c30c8b` | pass | 151 | 79.8% | $4.3185 |

## Comparable decision groups

| Profile | Rounds | RAG flags | Runs | Invest / Pass / Conditional |
|---|---:|---|---:|---:|
| opus_low | 60 | with=0, no=0 | 1 | 0 / 1 / 0 |
| routed | 60 | with=0, no=0 | 1 | 0 / 1 / 0 |

## Interpretation

Grounding counts all extracted factual and numeric claims; a claim marked unverifiable does not count as grounded. Citation validity checks every claim evidence ID against retrieval records for the same turn. Decision stability groups runs by model profile, prompt version and round count; five comparable live runs are required before grading it.
