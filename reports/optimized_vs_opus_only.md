# Optimized (routed) vs Opus-only

Company `biobot`. Both columns are measured from the recorded API usage of each run; nothing is estimated. Each meeting runs until the VC says it is ready to decide, so the two runs can differ in length; compare per-turn figures as well as totals.

| Metric | Optimized (routed) | Opus only |
|---|---:|---:|
| Models | JEV (moderator, fact-check verdicts) + Haiku (claim extraction) + Sonnet (debate, escalations) + Opus (decision) | Opus for every call, the moderator included |
| Run | `4a2867c30c8b` | `1f3fc5752d73` |
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
| Optimized saves (cost) | $8.8747 (67.3% of Opus-only) |  |
| Optimized vs Opus-only (wall-clock) | -2,133s (-62.0%) |  |

Claude input tokens exclude cache writes and reads, which are listed and priced separately; Claude output tokens include thinking. JEV bills input tokens only ($0.042 per million), so its output tokens are shown but not priced.
