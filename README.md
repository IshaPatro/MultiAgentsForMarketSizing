# MultiAgentsForMarketSizing

Agentic workflows for venture-capital work, built on Claude. Three independent sub-projects share one
public website.

| Folder | What it is | Start here |
|---|---|---|
| [`boardroom/`](boardroom/) | **NextGen Boardroom**: multi-agent VC debate (CEO / CFO / CTO / Devil's Advocate questioned by a VC) on LangGraph, with RAG over a case corpus, fact-checking and a final investment decision | [`boardroom/README.md`](boardroom/README.md) |
| [`three_statement_forecast/`](three_statement_forecast/) | Agent that turns a company's messy raw exports into an Inputs-driven 36-month three-statement model, using Anthropic's `xlsx` skill plus a custom house-style skill | [`three_statement_forecast/README.md`](three_statement_forecast/README.md) |
| [`portfolio_monitoring/`](portfolio_monitoring/) | Quarterly portfolio monitoring: mixed-format company updates in, fund-standard dashboard out; generator agent validated by a second agent (Jev) and deterministic checks | [`portfolio_monitoring/README.md`](portfolio_monitoring/README.md) |
| [`web/`](web/) | Public static site (GitHub Pages), shared by all sub-projects | `.github/workflows/jekyll-gh-pages.yml` |

Each sub-project has its own virtual environment (`<project>/.venv`), dependencies and README; run commands
from inside the sub-project folder. Sample data in every sub-project is fictional unless its README says otherwise.
