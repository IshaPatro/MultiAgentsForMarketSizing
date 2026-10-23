# Inference metrics: stored runs compared

Generated from `debate.sqlite` by `scripts/inference_metrics.py`. 2 runs. Every value is read from the recorded API usage of each call; nothing is estimated.

| Metric | `4a2867c30c8b` | `1f3fc5752d73` |
|---|---:|---:|
| Kind | live (billed) | live (billed) |
| Profile | routed | opus_low |
| Status | completed | completed |
| Wall-clock time for the whole debate (s) | 1,305 | 3,438 |
| Model-call latency, summed over all calls (s) | 1,293 | 2,913 |
| VC turns (opening, questions, closing remark) | 31 | 29 |
| Debate turns (VC + executives + devil) | 91 | 85 |
| API requests: Claude | 213 | 237 |
| API requests: JEV | 630 | 0 |
| System-1 requests (claim extraction, verdicts, moderator) | 721 | 0 |
| Claude input tokens (uncached) | 1,325,721 | 2,162,566 |
| Claude cache-write tokens | 19,025 | 37,696 |
| Claude cache-read tokens | 283,435 | 277,130 |
| Claude output tokens (incl. thinking) | 170,598 | 214,951 |
| JEV input tokens (billed) | 1,193,544 | 0 |
| JEV output tokens (reported by JEV, not billed) | 118,456 | 0 |
| Total tokens processed (all of the above) | 3,110,779 | 2,692,343 |
| Cost: Claude (USD) | $4.2684 | $13.1932 |
| Cost: JEV (USD) | $0.050129 | $0.000000 |
| Cost: total (USD) | $4.3185 | $13.1932 |
| Cost check | matches the sum of all recorded calls | matches the sum of all recorded calls |
| Cost per VC turn (USD) | $0.1393 | $0.4549 |
| Avg / max words per answer (executives, devil) | 315 / 488 | 308 / 470 |
| Moderator picks for main questions (CEO / CFO / CTO) | 8 / 4 / 1 | 8 / 4 / 1 |
| Claims: supported / unverifiable / contradicted / miscalculated | 492 / 115 / 41 / 12 | 708 / 64 / 19 / 2 |
| Claims settled in code (calculations) | 43 | 96 |
| Claims escalated to the stronger model | 264 | 101 |
| VC decision | pass | pass |

## Live run `4a2867c30c8b` by tier and model

| Tier | Model | Requests | Input tok | Cache-write tok | Cache-read tok | Output tok | Latency (s) | Cost (USD) | Share of cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | `claude-sonnet-5-5` | 91 | 555,390 | 15,412 | 186,835 | 62,223 | 613.1 | $1.808907 | 41.9% |
| decision | `claude-opus-5-5` | 1 | 315,927 | 1,889 | 0 | 4,644 | 50.1 | $1.366033 | 31.6% |
| check_hard | `claude-sonnet-5-5` | 30 | 396,345 | 960 | 27,840 | 24,666 | 185.6 | $1.047318 | 24.3% |
| verdict | `jev:jev-latest` | 617 | 1,186,985 | 0 | 0 | 117,898 (not billed) | 82.7 | $0.049853 | 1.2% |
| fast | `claude-haiku-5-5` | 91 | 58,059 | 764 | 68,760 | 79,065 | 360.0 | $0.046121 | 1.1% |
| route | `jev:jev-latest` | 13 | 6,559 | 0 | 0 | 558 (not billed) | 1.7 | $0.000275 | 0.0% |

## Live run `1f3fc5752d73` by tier and model

| Tier | Model | Requests | Input tok | Cache-write tok | Cache-read tok | Output tok | Latency (s) | Cost (USD) | Share of cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| verdict | `claude-opus-5-5` | 28 | 1,084,985 | 1,920 | 24,960 | 56,489 | 488.4 | $5.484312 | 41.6% |
| debate | `claude-opus-5-5` | 85 | 544,894 | 31,121 | 157,949 | 66,162 | 1,411.6 | $3.690011 | 28.0% |
| fast | `claude-opus-5-5` | 85 | 59,725 | 1,528 | 63,412 | 74,328 | 740.6 | $1.745782 | 13.2% |
| decision | `claude-opus-5-5` | 1 | 318,732 | 1,889 | 0 | 5,408 | 57.5 | $1.392533 | 10.6% |
| check_hard | `claude-opus-5-5` | 25 | 151,579 | 0 | 24,000 | 11,901 | 185.1 | $0.849136 | 6.4% |
| route | `claude-opus-5-5` | 13 | 2,651 | 1,238 | 6,809 | 663 | 29.6 | $0.031416 | 0.2% |
