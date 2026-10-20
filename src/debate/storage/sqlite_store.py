"""Transcript store. SQLite in Phase 1; the same tables move to Postgres (db/init.sql) in Phase 5."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from debate.state import Turn

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, era TEXT NOT NULL, cutoff_date TEXT NOT NULL,
    thread_id TEXT NOT NULL, config_json TEXT NOT NULL, started_at TEXT NOT NULL,
    ended_at TEXT, status TEXT NOT NULL, total_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turns (
    run_id TEXT NOT NULL REFERENCES runs(run_id), seq INTEGER NOT NULL, round INTEGER NOT NULL,
    speaker TEXT NOT NULL, addressed_to TEXT, content TEXT NOT NULL, model TEXT NOT NULL, tier TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL, cost_usd REAL NOT NULL, prompt_version TEXT NOT NULL,
    retries INTEGER NOT NULL, trimmed INTEGER NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def start_run(self, *, run_id: str, company_id: str, era: str, cutoff_date: str, thread_id: str, config: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,NULL,'running',0)",
            (run_id, company_id, era, cutoff_date, thread_id, json.dumps(config), _now()),
        )
        self.conn.commit()

    def add_turn(self, run_id: str, t: Turn) -> None:
        u = t.usage
        self.conn.execute(
            "INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, t.seq, t.round, t.speaker, t.addressed_to, t.content, t.model, t.tier,
             u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
             u.latency_ms, u.cost_usd, t.prompt_version, t.retries, int(t.trimmed), _now()),
        )
        self.conn.commit()

    def end_run(self, run_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE runs SET ended_at=?, status=?, total_cost_usd="
            "(SELECT COALESCE(SUM(cost_usd),0) FROM turns WHERE run_id=?) WHERE run_id=?",
            (_now(), status, run_id, run_id),
        )
        self.conn.commit()

    def turns(self, run_id: str) -> list[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        return self.conn.execute("SELECT * FROM turns WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
