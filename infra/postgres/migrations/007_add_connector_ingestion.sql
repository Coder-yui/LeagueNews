BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version varchar(100) PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS connector_runs (
    id serial PRIMARY KEY,
    source_id integer NOT NULL REFERENCES sources(id),
    connector_type varchar(60) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'running',
    discovered_count integer NOT NULL DEFAULT 0,
    created_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_connector_runs_source_id ON connector_runs(source_id);
CREATE INDEX IF NOT EXISTS ix_connector_runs_connector_type ON connector_runs(connector_type);
CREATE INDEX IF NOT EXISTS ix_connector_runs_status ON connector_runs(status);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM schema_migrations
        WHERE version = '019_version_raw_item_ingestion'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_items_source_external_id
            ON raw_items(source_id, external_id)
            WHERE external_id IS NOT NULL;
    END IF;
END $$;

INSERT INTO schema_migrations(version)
SELECT '007_add_connector_ingestion'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '007_add_connector_ingestion'
);

COMMIT;
