BEGIN;

ALTER TABLE raw_items DROP COLUMN IF EXISTS author_handle;
ALTER TABLE raw_items DROP COLUMN IF EXISTS author_external_id;
ALTER TABLE raw_items DROP COLUMN IF EXISTS author_url;
ALTER TABLE raw_items DROP COLUMN IF EXISTS content_schema_version;

INSERT INTO schema_migrations(version)
VALUES ('017_simplify_raw_item_identity')
ON CONFLICT (version) DO NOTHING;

COMMIT;
