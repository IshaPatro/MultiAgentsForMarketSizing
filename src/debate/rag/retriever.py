"""Hybrid retrieval with a hard era filter (implementation.md sections 7 and 9).

The era is a required argument: there is no code path that searches without a cutoff. The filter is SQL, so
the model never sees a forbidden chunk, and every result is re-checked in Python (fail loudly, not silently).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from debate.config import EraContext
from debate.rag.embeddings import Embedder

RRF_K = 60
CANDIDATES = 20
_WORD = re.compile(r"[A-Za-z0-9]{3,}")
_STOP = {"the", "and", "for", "that", "with", "this", "from", "have", "are", "was", "what", "how", "why", "you",
         "your", "does", "will", "would", "their", "they", "not", "but", "can", "its", "our", "has", "had"}


class LeakError(RuntimeError):
    """A chunk dated after the era's cutoff (or undated) came back from retrieval."""


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page: int | None
    section: str | None
    doc_type: str
    as_of_date: date | None
    score: float = 0.0


@dataclass
class Retrieval:
    query: str
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [c.chunk_id for c in self.chunks]

    @property
    def max_as_of_date(self) -> date | None:
        return max((c.as_of_date for c in self.chunks if c.as_of_date), default=None)


class Retriever(Protocol):
    def search(self, query: str, era: EraContext, company_id: str, k: int) -> Retrieval: ...


class NullRetriever:
    def search(self, query: str, era: EraContext, company_id: str, k: int) -> Retrieval:
        return Retrieval(query=query)


def assert_no_leak(chunks: list[Chunk], era: EraContext) -> None:
    for c in chunks:
        if c.as_of_date is None or c.as_of_date > era.cutoff_date:
            raise LeakError(f"chunk {c.chunk_id} (as_of={c.as_of_date}) is not allowed in era {era.name} "
                            f"(cutoff {era.cutoff_date})")


def or_query(text: str) -> str:
    """Plain words OR-ed together for to_tsquery; plainto_tsquery would AND them and match almost nothing."""
    words = [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP]
    return " | ".join(dict.fromkeys(words))


_FILTER = ("company_id = %(company)s AND embedding_model = %(embedding_model)s "
           "AND needs_review = FALSE AND as_of_date IS NOT NULL "
           "AND as_of_date <= %(cutoff)s AND %(era)s = ANY(era_tags)")
_COLS = "chunk_id, text, page, section, doc_type, as_of_date"

DENSE_SQL = f"SELECT {_COLS} FROM chunks WHERE {_FILTER} ORDER BY embedding <=> %(vec)s::vector LIMIT {CANDIDATES}"
LEXICAL_SQL = (f"SELECT {_COLS} FROM chunks WHERE {_FILTER} AND tsv @@ to_tsquery('english', %(tsq)s) "
               f"ORDER BY ts_rank_cd(tsv, to_tsquery('english', %(tsq)s)) DESC LIMIT {CANDIDATES}")


class PgRetriever:
    def __init__(self, conn, embedder: Embedder):
        self.conn, self.embedder = conn, embedder

    def search(self, query: str, era: EraContext, company_id: str, k: int) -> Retrieval:
        params = {"company": company_id, "cutoff": era.cutoff_date, "era": era.name,
                  "embedding_model": self.embedder.name}
        dense = self.conn.execute(DENSE_SQL, {**params, "vec": self.embedder.embed_query(query)}).fetchall()
        tsq = or_query(query)
        lexical = self.conn.execute(LEXICAL_SQL, {**params, "tsq": tsq}).fetchall() if tsq else []

        # Reciprocal Rank Fusion: needs no score calibration between cosine distance and ts_rank.
        scores: dict[str, float] = {}
        rows = {}
        for ranking in (dense, lexical):
            for rank, row in enumerate(ranking):
                rows[row[0]] = row
                scores[row[0]] = scores.get(row[0], 0.0) + 1.0 / (RRF_K + rank + 1)
        top = sorted(scores, key=scores.get, reverse=True)[:k]
        chunks = [Chunk(*rows[cid], score=scores[cid]) for cid in top]
        assert_no_leak(chunks, era)
        return Retrieval(query=query, chunks=chunks)
