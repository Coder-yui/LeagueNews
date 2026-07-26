BEGIN;

ALTER TABLE raw_items
    ADD COLUMN IF NOT EXISTS content_hash_version integer NOT NULL DEFAULT 1;
ALTER TABLE raw_items
    ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE raw_items
    ADD COLUMN IF NOT EXISTS supersedes_raw_item_id integer
        REFERENCES raw_items(id) ON DELETE SET NULL;

DROP INDEX IF EXISTS uq_raw_items_source_external_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_items_source_external_hash
    ON raw_items(source_id, external_id, content_hash)
    WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_items_source_hash_without_external
    ON raw_items(source_id, content_hash)
    WHERE external_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_raw_items_supersedes_raw_item_id
    ON raw_items(supersedes_raw_item_id);

ALTER TABLE connector_runs
    ADD COLUMN IF NOT EXISTS revised_count integer NOT NULL DEFAULT 0;

INSERT INTO schema_migrations(version)
VALUES ('019_version_raw_item_ingestion')
ON CONFLICT (version) DO NOTHING;

COMMIT;
