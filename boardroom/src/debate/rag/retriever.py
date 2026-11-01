"""Hybrid retrieval over the company's corpus (implementation.md section 7).

Dense (pgvector) and lexical (Okapi BM25) candidates are merged with Reciprocal Rank Fusion. The SQL filter keeps
each search inside one company's corpus and one embedding model. BM25 runs in process over that company's chunks:
the corpus is a few hundred chunks, and BM25's IDF weighting is what lets rare terms ("hedge funds", "50,427")
beat common ones ("public", "health", "data"), which Postgres ts_rank cannot do.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from debate.rag.embeddings import Embedder

# Much smaller than the textbook 60 (tuned for ~1000-deep lists): with a few dozen candidates per list, a large k
# lets chunks ranked ~10th in both lists beat a decisive #1 in one list (e.g. 12 near-identical Exhibit 7 rows
# outranking the fact that answers the question). On eval/retrieval_questions.yaml, hit@4 by k:
#   case corpus, 41 questions, 20 candidates:      k=60/30 90%, k=20/10 93%, k=5 95%
#   + public ledger, 49 questions, 40 candidates:  k=60/30/20/10 92%, k=5 96% (20 BM25 candidates, k=5: 94%)
# k=5 was best on both corpus versions, so it replaced the earlier, more cautious k=20.
RRF_K = 5
CANDIDATES = 40
_WORD = re.compile(r"[A-Za-z0-9]{3,}")
_STOP = {"the", "and", "for", "that", "with", "this", "from", "have", "are", "was", "what", "how", "why", "you",
         "your", "does", "will", "would", "their", "they", "not", "but", "can", "its", "our", "has", "had",
         "which", "who", "whom", "when", "where", "could", "should", "might", "did", "do", "is", "be", "been",
         "it", "of", "to", "in", "on", "an", "or", "as", "at", "by", "if", "into", "about", "there", "these", "those"}


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page: int | None
    section: str | None
    doc_type: str           # narrative | fact | exhibit_row | exhibit_caption | endnote | assumption | sizing_model
    score: float = 0.0
    topic: str | None = None
    as_of: str | None = None  # YYYY[-MM[-DD]]: the date the information refers to


@dataclass
class Retrieval:
    query: str
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [c.chunk_id for c in self.chunks]


# Agents see a handful of chunks. Assumptions and sizing models are dense with sizing vocabulary and would otherwise
# fill every slot on a market-size question, leaving no case facts; each type gets at most one slot. Unverified
# public leads (screenshot transcriptions) get one slot too, so they never outnumber sourced evidence.
EVIDENCE_CAPS = {"assumption": 1, "sizing_model": 1, "public_lead": 1}


def diversify(chunks: list[Chunk], k: int, caps: dict[str, int] = EVIDENCE_CAPS) -> list[Chunk]:
    """Keep ranking order, but allow at most caps[type] chunks of a capped type among the k returned."""
    used: dict[str, int] = {}
    out = []
    for c in chunks:
        if c.doc_type in caps and used.get(c.doc_type, 0) >= caps[c.doc_type]:
            continue
        used[c.doc_type] = used.get(c.doc_type, 0) + 1
        out.append(c)
        if len(out) == k:
            break
    return out


class Retriever(Protocol):
    def search(self, query: str, company_id: str, k: int) -> Retrieval: ...


class NullRetriever:
    def search(self, query: str, company_id: str, k: int) -> Retrieval:
        return Retrieval(query=query)

    def by_ids(self, ids: list[str], company_id: str) -> list[Chunk]:
        return []


_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase words and numbers ("50,427", "3.6"), stopwords dropped, plural -s folded ("funds" -> "fund")."""
    out = []
    for t in _TOKEN.findall(text.lower()):
        if t in _STOP or len(t) < 2:
            continue
        out.append(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") and t.isalpha() else t)
    return out


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) over an in-memory list of (chunk_id, text)."""

    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        self.ids = [d[0] for d in docs]
        self.tf = [Counter(tokenize(d[1])) for d in docs]
        self.len = [sum(tf.values()) for tf in self.tf]
        self.avg = sum(self.len) / len(self.len) if self.len else 0.0
        df = Counter(term for tf in self.tf for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def top(self, query: str, k: int) -> list[str]:
        terms = [t for t in dict.fromkeys(tokenize(query)) if t in self.idf]
        scored = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        return [self.ids[i] for _, i in sorted(scored, reverse=True)[:k]]


_FILTER = "company_id = %(company)s AND embedding_model = %(embedding_model)s"
_COLS = "chunk_id, text, page, section, doc_type, topic, as_of"

DENSE_SQL = f"SELECT {_COLS} FROM chunks WHERE {_FILTER} ORDER BY embedding <=> %(vec)s::vector LIMIT {CANDIDATES}"
ALL_SQL = f"SELECT {_COLS} FROM chunks WHERE {_FILTER}"


def _chunk(row, score: float = 0.0) -> Chunk:
    cid, text, page, section, doc_type, topic, as_of = row
    return Chunk(cid, text, page, section, doc_type, score, topic, as_of)


class PgRetriever:
    def __init__(self, conn, embedder: Embedder):
        self.conn, self.embedder = conn, embedder
        self._lexical: dict[str, tuple[BM25, dict]] = {}  # company -> (index, chunk rows); built on first use

    def _bm25(self, company_id: str) -> tuple[BM25, dict]:
        if company_id not in self._lexical:
            rows = self.conn.execute(ALL_SQL, {"company": company_id, "embedding_model": self.embedder.name}).fetchall()
            self._lexical[company_id] = (BM25([(r[0], r[1]) for r in rows]), {r[0]: r for r in rows})
        return self._lexical[company_id]

    def search(self, query: str, company_id: str, k: int) -> Retrieval:
        params = {"company": company_id, "embedding_model": self.embedder.name}
        dense = self.conn.execute(DENSE_SQL, {**params, "vec": self.embedder.embed_query(query)}).fetchall()
        index, by_id = self._bm25(company_id)
        lexical = [by_id[cid] for cid in index.top(query, CANDIDATES)]

        # Reciprocal Rank Fusion: needs no score calibration between cosine distance and ts_rank.
        scores: dict[str, float] = {}
        rows = {}
        for ranking in (dense, lexical):
            for rank, row in enumerate(ranking):
                rows[row[0]] = row
                scores[row[0]] = scores.get(row[0], 0.0) + 1.0 / (RRF_K + rank + 1)
        top = sorted(scores, key=scores.get, reverse=True)[:k]
        return Retrieval(query=query, chunks=[_chunk(rows[cid], scores[cid]) for cid in top])

    def by_ids(self, ids: list[str], company_id: str) -> list[Chunk]:
        if not ids:
            return []
        rows = self.conn.execute(f"SELECT {_COLS} FROM chunks WHERE {_FILTER} AND chunk_id = ANY(%(ids)s)",
                                 {"company": company_id, "embedding_model": self.embedder.name, "ids": ids}).fetchall()
        return [_chunk(row) for row in rows]
