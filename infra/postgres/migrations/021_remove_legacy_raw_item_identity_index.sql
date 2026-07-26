BEGIN;

DROP INDEX IF EXISTS uq_raw_items_source_external_id;

INSERT INTO schema_migrations(version)
VALUES ('021_remove_legacy_raw_item_identity_index')
ON CONFLICT (version) DO NOTHING;

COMMIT;
