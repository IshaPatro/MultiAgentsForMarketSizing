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
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, turn_seq INTEGER NOT NULL, speaker TEXT NOT NULL,
    text TEXT NOT NULL, type TEXT NOT NULL, verdict TEXT NOT NULL, confidence REAL, evidence_ids TEXT NOT NULL,
    note TEXT, escalated INTEGER NOT NULL, checker_model TEXT
);
CREATE TABLE IF NOT EXISTS retrievals (
    run_id TEXT NOT NULL, turn_seq INTEGER NOT NULL, kind TEXT NOT NULL, query TEXT NOT NULL,
    chunk_ids TEXT NOT NULL, scores TEXT NOT NULL, max_as_of_date TEXT
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

# Columns added after Phase 1; added in place so older scratch databases keep working.
TURN_COLUMNS = {
    "evidence_ids": "TEXT NOT NULL DEFAULT '[]'", "invalid_citations": "INTEGER NOT NULL DEFAULT 0",
    "blocked": "INTEGER NOT NULL DEFAULT 0", "regenerations": "INTEGER NOT NULL DEFAULT 0",
    "check_cost_usd": "REAL NOT NULL DEFAULT 0", "claims_json": "TEXT NOT NULL DEFAULT '[]'",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(turns)")}
        for col, ddl in TURN_COLUMNS.items():
            if col not in have:
                self.conn.execute(f"ALTER TABLE turns ADD COLUMN {col} {ddl}")
        self.conn.commit()

    def start_run(self, *, run_id: str, company_id: str, era: str, cutoff_date: str, thread_id: str, config: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,NULL,'running',0)",
            (run_id, company_id, era, cutoff_date, thread_id, json.dumps(config), _now()),
        )
        self.conn.commit()

    def add_turn(self, run_id: str, t: Turn) -> None:
        u = t.usage
        self.conn.execute(
            "INSERT OR REPLACE INTO turns (run_id, seq, round, speaker, addressed_to, content, model, tier,"
            " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, latency_ms, cost_usd,"
            " prompt_version, retries, trimmed, created_at, evidence_ids, invalid_citations, blocked,"
            " regenerations, check_cost_usd, claims_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, t.seq, t.round, t.speaker, t.addressed_to, t.content, t.model, t.tier,
             u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
             u.latency_ms, u.cost_usd, t.prompt_version, t.retries, int(t.trimmed), _now(),
             json.dumps(t.evidence_ids), t.invalid_citations, int(t.blocked), t.regenerations, t.check_cost_usd,
             json.dumps([c.model_dump() for c in t.claims])),
        )
        self.conn.commit()

    def add_claims(self, run_id: str, claims: list[dict]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(c["claim_id"], run_id, c["turn_seq"], c["speaker"], c["text"], c["type"], c["verdict"], c["confidence"],
              json.dumps(c["evidence_ids"]), c["note"], int(c["escalated"]), c["checker_model"]) for c in claims])
        self.conn.commit()

    def add_retrieval(self, run_id: str, seq: int, kind: str, r) -> None:
        self.conn.execute(
            "INSERT INTO retrievals VALUES (?,?,?,?,?,?,?)",
            (run_id, seq, kind, r.query, json.dumps(r.ids), json.dumps([round(c.score, 5) for c in r.chunks]),
             r.max_as_of_date.isoformat() if r.max_as_of_date else None))
        self.conn.commit()

    def add_event(self, run_id: str, type_: str, payload: dict) -> None:
        self.conn.execute("INSERT INTO events VALUES (?,?,?,?)", (run_id, type_, json.dumps(payload), _now()))
        self.conn.commit()

    def end_run(self, run_id: str, status: str, total_cost_usd: float | None = None) -> None:
        """total_cost_usd should be the run's full spend (turns + fact-checks); defaults to the turns' sum."""
        self.conn.execute(
            "UPDATE runs SET ended_at=?, status=?, total_cost_usd=COALESCE(?, "
            "(SELECT COALESCE(SUM(cost_usd),0) FROM turns WHERE run_id=?)) WHERE run_id=?",
            (_now(), status, total_cost_usd, run_id, run_id),
        )
        self.conn.commit()

    def turns(self, run_id: str) -> list[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        return self.conn.execute("SELECT * FROM turns WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
