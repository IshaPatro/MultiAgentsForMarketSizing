"""Postgres-backed LangGraph checkpointer and long-term store."""
from __future__ import annotations

import os
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from langchain_core.embeddings import Embeddings

from debate.rag.db import DEFAULT_URL
from debate.rag.embeddings import DIM, FastEmbedder


class LocalMemoryEmbeddings(Embeddings):
    def __init__(self):
        self.embedder = FastEmbedder()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embedder.embed_docs(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embedder.embed_query(text)


@contextmanager
def persistence():
    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    index = {"dims": DIM, "embed": LocalMemoryEmbeddings(), "fields": ["summary"]}
    with PostgresSaver.from_conn_string(url) as saver, PostgresStore.from_conn_string(url, index=index) as store:
        saver.setup()
        store.setup()
        yield saver, store
