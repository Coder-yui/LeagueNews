BEGIN;

-- The immutable v1 baseline required by 002_content_pipeline_v2. Keeping the
-- baseline in the ledger makes a fresh installation follow the same ordered
-- upgrade path as every existing database.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version varchar(100) PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (
    id serial PRIMARY KEY,
    name varchar(120) NOT NULL UNIQUE,
    connector_type varchar(60) NOT NULL DEFAULT 'manual',
    base_url varchar(500),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_sources_name ON sources(name);
CREATE INDEX ix_sources_connector_type ON sources(connector_type);

CREATE TABLE raw_items (
    id serial PRIMARY KEY,
    source_id integer NOT NULL REFERENCES sources(id),
    external_id varchar(255),
    url varchar(1000),
    title varchar(500),
    content text NOT NULL,
    published_at timestamptz,
    status varchar(30) NOT NULL DEFAULT 'pending',
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_raw_items_source_id ON raw_items(source_id);
CREATE INDEX ix_raw_items_external_id ON raw_items(external_id);
CREATE INDEX ix_raw_items_status ON raw_items(status);

CREATE TABLE news_events (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL REFERENCES raw_items(id) ON DELETE CASCADE,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    category varchar(60) NOT NULL,
    entities json NOT NULL DEFAULT '[]'::json,
    importance_score double precision NOT NULL,
    credibility varchar(30) NOT NULL,
    occurred_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX ix_news_events_raw_item_id ON news_events(raw_item_id);
CREATE INDEX ix_news_events_category ON news_events(category);

INSERT INTO schema_migrations(version)
VALUES ('001_initial_schema');

COMMIT;
