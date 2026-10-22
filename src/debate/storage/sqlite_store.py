"""Transcript store. SQLite in Phase 1; the same tables move to Postgres (db/init.sql) in Phase 5."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from debate.state import Turn
from debate.guards import word_count

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, thread_id TEXT NOT NULL, config_json TEXT NOT NULL, started_at TEXT NOT NULL,
    ended_at TEXT, status TEXT NOT NULL, total_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turns (
    run_id TEXT NOT NULL REFERENCES runs(run_id), seq INTEGER NOT NULL, round INTEGER NOT NULL,
    speaker TEXT NOT NULL, addressed_to TEXT, content TEXT NOT NULL, model TEXT NOT NULL, tier TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL, cost_usd REAL NOT NULL, prompt_version TEXT NOT NULL,
    retries INTEGER NOT NULL, trimmed INTEGER NOT NULL, created_at TEXT NOT NULL,
    word_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, turn_seq INTEGER NOT NULL, speaker TEXT NOT NULL,
    text TEXT NOT NULL, type TEXT NOT NULL, verdict TEXT NOT NULL, confidence REAL, evidence_ids TEXT NOT NULL,
    note TEXT, escalated INTEGER NOT NULL, checker_model TEXT
);
CREATE TABLE IF NOT EXISTS retrievals (
    run_id TEXT NOT NULL, turn_seq INTEGER NOT NULL, kind TEXT NOT NULL, query TEXT NOT NULL,
    chunk_ids TEXT NOT NULL, scores TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id),
    role TEXT NOT NULL, tier TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL, cost_usd REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id), decision_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

# Columns added after Phase 1; added in place so older scratch databases keep working.
TURN_COLUMNS = {
    "evidence_ids": "TEXT NOT NULL DEFAULT '[]'", "invalid_citations": "INTEGER NOT NULL DEFAULT 0",
    "check_cost_usd": "REAL NOT NULL DEFAULT 0", "claims_json": "TEXT NOT NULL DEFAULT '[]'",
    "word_count": "INTEGER NOT NULL DEFAULT 0",
    "calculation": "TEXT NOT NULL DEFAULT ''", "assumptions_json": "TEXT NOT NULL DEFAULT '[]'",
    "calculation_ok": "INTEGER",  # NULL when there was nothing checkable
    "estimates_json": "TEXT NOT NULL DEFAULT '[]'", "topic": "TEXT NOT NULL DEFAULT ''",
    "is_follow_up": "INTEGER NOT NULL DEFAULT 0",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SqliteStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        if "era" in {r[1] for r in self.conn.execute("PRAGMA table_info(runs)")}:
            raise RuntimeError(f"{path} was created for the removed pre/post-COVID study (runs.era is NOT NULL); "
                               "delete it with `python -m debate reset --yes` and run again")
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(turns)")}
        for col, ddl in TURN_COLUMNS.items():
            if col not in have:
                self.conn.execute(f"ALTER TABLE turns ADD COLUMN {col} {ddl}")
        for run_id, seq, content in self.conn.execute(
            "SELECT run_id, seq, content FROM turns WHERE word_count=0"
        ).fetchall():
            self.conn.execute("UPDATE turns SET word_count=? WHERE run_id=? AND seq=?",
                              (word_count(content), run_id, seq))
        self.conn.commit()

    def start_run(self, *, run_id: str, company_id: str, thread_id: str, config: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, company_id, thread_id, config_json, started_at, ended_at, status,"
            " total_cost_usd) VALUES (?,?,?,?,?,NULL,'running',0)",
            (run_id, company_id, thread_id, json.dumps(config), _now()),
        )
        self.conn.commit()

    def add_turn(self, run_id: str, t: Turn) -> None:
        u = t.usage
        self.conn.execute(
            "INSERT OR REPLACE INTO turns (run_id, seq, round, speaker, addressed_to, content, model, tier,"
            " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, latency_ms, cost_usd,"
            " prompt_version, retries, trimmed, created_at, evidence_ids, invalid_citations,"
            " check_cost_usd, claims_json, word_count, calculation, assumptions_json, calculation_ok,"
            " estimates_json, topic, is_follow_up)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, t.seq, t.round, t.speaker, t.addressed_to, t.content, t.model, t.tier,
             u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
             u.latency_ms, u.cost_usd, t.prompt_version, t.retries, int(t.trimmed), _now(),
             json.dumps(t.evidence_ids), t.invalid_citations, t.check_cost_usd,
             json.dumps([c.model_dump() for c in t.claims]), word_count(t.content),
             t.calculation, json.dumps(t.assumptions), None if t.calculation_ok is None else int(t.calculation_ok),
             json.dumps(t.estimates), t.topic, int(t.follow_up)),
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
            "INSERT INTO retrievals VALUES (?,?,?,?,?,?)",
            (run_id, seq, kind, r.query, json.dumps(r.ids), json.dumps([round(c.score, 5) for c in r.chunks])))
        self.conn.commit()

    def add_event(self, run_id: str, type_: str, payload: dict) -> None:
        self.conn.execute("INSERT INTO events VALUES (?,?,?,?)", (run_id, type_, json.dumps(payload), _now()))
        self.conn.commit()

    def add_model_call(self, run_id: str, role: str, tier: str, model: str, usage) -> None:
        self.conn.execute(
            "INSERT INTO model_calls (run_id, role, tier, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, latency_ms, cost_usd, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, role, tier, model, usage.input_tokens, usage.output_tokens,
             usage.cache_read_tokens, usage.cache_write_tokens, usage.latency_ms, usage.cost_usd, _now()),
        )
        self.conn.commit()

    def add_decision(self, run_id: str, decision: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?)", (run_id, json.dumps(decision), _now()))
        self.conn.commit()

    def get_decision(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT decision_json FROM decisions WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def call_stats(self, run_id: str) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN tier IN ('fast','verdict') AND model <> 'claude-opus-5-5' THEN 1 ELSE 0 END), "
            "COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(cost_usd),0) FROM model_calls WHERE run_id=?", (run_id,),
        ).fetchone()
        return {"calls": row[0], "system1_calls": row[1] or 0, "input_tokens": row[2],
                "output_tokens": row[3], "cost_usd": row[4]}

    def end_run(self, run_id: str, status: str, total_cost_usd: float | None = None) -> None:
        """total_cost_usd should be the run's full spend (turns + fact-checks); defaults to the turns' sum."""
        self.conn.execute(
            "UPDATE runs SET ended_at=?, status=?, total_cost_usd=COALESCE(?, "
            "(SELECT COALESCE(SUM(cost_usd),0) FROM turns WHERE run_id=?)) WHERE run_id=?",
            (_now(), status, total_cost_usd, run_id, run_id),
        )
        self.conn.commit()

    def pause_run(self, run_id: str, total_cost_usd: float) -> None:
        self.conn.execute("UPDATE runs SET status='paused', total_cost_usd=? WHERE run_id=?",
                          (total_cost_usd, run_id))
        self.conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT run_id, company_id, thread_id, config_json, status, total_cost_usd FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "company_id": row[1], "thread_id": row[2], "config": json.loads(row[3]),
                "status": row[4], "total_cost_usd": row[5]}

    def turns(self, run_id: str) -> list[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        return self.conn.execute("SELECT * FROM turns WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
