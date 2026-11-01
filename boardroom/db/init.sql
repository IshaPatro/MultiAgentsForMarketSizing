-- Schema from implementation.md section 12. Runs once on first container start.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE runs (
    run_id         TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    config_json    JSONB NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running',
    total_cost_usd NUMERIC(10,5) NOT NULL DEFAULT 0
);

CREATE TABLE turns (
    turn_id             BIGSERIAL PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(run_id),
    round               INT NOT NULL,
    seq                 INT NOT NULL,
    speaker             TEXT NOT NULL,
    addressed_to        TEXT,
    content             TEXT NOT NULL,
    evidence_ids        TEXT[] NOT NULL DEFAULT '{}',
    model               TEXT NOT NULL,
    tier                TEXT NOT NULL,
    input_tokens        INT NOT NULL,
    output_tokens       INT NOT NULL,
    cache_read_tokens   INT NOT NULL DEFAULT 0,
    cache_write_tokens  INT NOT NULL DEFAULT 0,
    latency_ms          INT NOT NULL,
    cost_usd            NUMERIC(10,6) NOT NULL,
    prompt_version      TEXT NOT NULL,
    retries             INT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

CREATE TABLE claims (
    claim_id   TEXT PRIMARY KEY,
    turn_id    BIGINT NOT NULL REFERENCES turns(turn_id),
    text       TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    speaker    TEXT NOT NULL
);

CREATE TABLE fact_checks (
    claim_id      TEXT NOT NULL REFERENCES claims(claim_id),
    verdict       TEXT NOT NULL,
    confidence    REAL,
    checker_model TEXT NOT NULL,
    escalated     BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_ids  TEXT[] NOT NULL DEFAULT '{}',
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE retrievals (
    retrieval_id  BIGSERIAL PRIMARY KEY,
    turn_id       BIGINT NOT NULL REFERENCES turns(turn_id),
    query         TEXT NOT NULL,
    chunk_ids     TEXT[] NOT NULL,
    scores        REAL[] NOT NULL
);

CREATE TABLE decisions (
    run_id        TEXT PRIMARY KEY REFERENCES runs(run_id),
    decision_json JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE events (
    event_id     BIGSERIAL PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    type         TEXT NOT NULL,
    payload_json JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
