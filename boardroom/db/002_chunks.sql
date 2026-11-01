-- RAG corpus (built by `debate build-corpus`, embedded by `debate ingest`). Idempotent: applied on every ingest and real run.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    source          TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    page            INT,
    section         TEXT,
    text            TEXT NOT NULL,
    topic           TEXT,                        -- founders_team, market, financials, ... (configs/corpus/*.yaml)
    as_of           TEXT,                        -- YYYY[-MM[-DD]]: the date the information refers to
    meta            JSONB NOT NULL DEFAULT '{}', -- pages, endnotes, fact kind/value/unit, exhibit row, ...
    audience        TEXT[] NOT NULL DEFAULT '{all}',
    embedding_model TEXT NOT NULL,
    embedding       vector(384) NOT NULL,
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS topic TEXT, ADD COLUMN IF NOT EXISTS as_of TEXT,
                   ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS chunks_company_type_idx ON chunks (company_id, doc_type);

-- Older databases carried per-chunk dating for the removed pre/post-COVID study.
DROP INDEX IF EXISTS chunks_company_date_idx;
ALTER TABLE chunks DROP COLUMN IF EXISTS as_of_date, DROP COLUMN IF EXISTS era_tags,
                   DROP COLUMN IF EXISTS needs_review, DROP COLUMN IF EXISTS review_note;

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_company_idx ON chunks (company_id);
-- No ANN (hnsw/ivfflat) index on purpose: with a few hundred chunks an exact scan is fast, and ANN indexes
-- apply the WHERE filter after the vector search, which can silently return too few rows.
