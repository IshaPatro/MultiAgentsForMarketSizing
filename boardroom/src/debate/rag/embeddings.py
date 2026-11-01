"""Embedding models behind one interface. Local only: free, private, deterministic."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

DIM = 384


class Embedder(Protocol):
    name: str

    def embed_docs(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """BAAI/bge-small-en-v1.5 via fastembed (ONNX, ~130 MB, no torch)."""

    name = "BAAI/bge-small-en-v1.5"

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(self.name)

    def embed_docs(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


class HashEmbedder:
    """Deterministic bag-of-words embedding for tests: no download, same text -> same vector."""

    name = "test-hash-384"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_docs(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
