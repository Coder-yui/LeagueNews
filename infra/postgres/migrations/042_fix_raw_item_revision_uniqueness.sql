BEGIN;

DROP INDEX IF EXISTS uq_raw_items_source_external_hash;

CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_items_source_external_revision
    ON raw_items (source_id, external_id, revision)
    WHERE external_id IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES ('042_fix_raw_item_revision_uniqueness')
ON CONFLICT (version) DO NOTHING;

COMMIT;
