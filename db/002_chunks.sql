-- RAG corpus. Idempotent: applied by `debate ingest` on every run.
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
    -- Date the information was knowable, not the publication date. NULL = undated: never retrievable.
    as_of_date      DATE,
    era_tags        TEXT[] NOT NULL DEFAULT '{}',
    audience        TEXT[] NOT NULL DEFAULT '{all}',
    needs_review    BOOLEAN NOT NULL DEFAULT FALSE,
    review_note     TEXT,
    embedding_model TEXT NOT NULL,
    embedding       vector(384) NOT NULL,
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_company_date_idx ON chunks (company_id, as_of_date);
-- No ANN (hnsw/ivfflat) index on purpose: with a few hundred chunks an exact scan is fast, and ANN indexes
-- apply the WHERE filter after the vector search, which can silently return too few rows.
