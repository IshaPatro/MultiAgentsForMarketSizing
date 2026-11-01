from __future__ import annotations

import os

import psycopg
from pgvector.psycopg import register_vector

from debate.config import ROOT

DEFAULT_URL = "postgresql://debate:debate@localhost:5432/debate"


def connect(url: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(url or os.environ.get("DATABASE_URL", DEFAULT_URL), autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")  # must exist before the type can be registered
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute((ROOT / "db" / "002_chunks.sql").read_text())
