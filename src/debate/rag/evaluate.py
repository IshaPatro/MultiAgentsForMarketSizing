"""Retrieval evaluation: does the corpus return the right chunk for questions an agent will actually ask?

hit@k = share of questions with any expected chunk in the top k; MRR = mean of 1/rank of the first expected chunk
(0 when none appears in the candidates). Reported overall and per question kind.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from debate.config import ROOT
from debate.rag.corpus import normalize

QUESTIONS = ROOT / "eval" / "retrieval_questions.yaml"


class Question(BaseModel):
    q: str
    kind: str
    expect: list[str]
    contains: list[str] = []   # alternative: a retrieved chunk whose text contains all of these answers it


def load_questions(path: Path = QUESTIONS) -> tuple[str, list[Question]]:
    raw = yaml.safe_load(path.read_text())
    return raw["company_id"], [Question(**q) for q in raw["questions"]]


def evaluate(retriever, company_id: str, questions: list[Question], k: int = 4, depth: int = 10) -> dict:
    """Run every question once at `depth` and score hit@k and MRR from the same ranking."""
    rows = []
    for item in questions:
        chunks = retriever.search(item.q, company_id, depth).chunks
        ranked = [c.chunk_id for c in chunks]
        phrases = [normalize(p).lower() for p in item.contains]

        def answers(c) -> bool:
            return c.chunk_id in item.expect or bool(phrases) and all(p in normalize(c.text).lower() for p in phrases)

        rank = next((i for i, c in enumerate(chunks, 1) if answers(c)), None)
        rows.append({"q": item.q, "kind": item.kind, "expect": item.expect, "rank": rank,
                     "hit": rank is not None and rank <= k, "rr": 1 / rank if rank else 0.0, "top": ranked[:k]})

    def summary(subset: list[dict]) -> dict:
        n = len(subset)
        return {"n": n, "hit_at_k": sum(r["hit"] for r in subset) / n if n else 0.0,
                "mrr": sum(r["rr"] for r in subset) / n if n else 0.0}

    kinds = sorted({r["kind"] for r in rows})
    return {"k": k, "overall": summary(rows), "by_kind": {kd: summary([r for r in rows if r["kind"] == kd]) for kd in kinds},
            "rows": rows}


def report_markdown(result: dict, title: str = "Retrieval evaluation") -> str:
    k = result["k"]
    o = result["overall"]
    lines = [f"# {title}", "", f"{o['n']} questions · hit@{k} **{o['hit_at_k']:.0%}** · MRR **{o['mrr']:.2f}**", "",
             f"| Question kind | n | hit@{k} | MRR |", "|---|---:|---:|---:|"]
    for kind, s in result["by_kind"].items():
        lines.append(f"| {kind} | {s['n']} | {s['hit_at_k']:.0%} | {s['mrr']:.2f} |")
    misses = [r for r in result["rows"] if not r["hit"]]
    lines += ["", f"## Misses ({len(misses)})", ""]
    lines += [f"- {r['q']} - expected {', '.join(f'`{e}`' for e in r['expect'])}; "
              f"{'rank ' + str(r['rank']) if r['rank'] else 'not in top 10'}; got {', '.join(f'`{t}`' for t in r['top'])}"
              for r in misses] or ["- none"]
    return "\n".join(lines) + "\n"
