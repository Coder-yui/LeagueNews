BEGIN;

ALTER TABLE normalized_items
    ADD COLUMN IF NOT EXISTS approved_media_extraction_ids json NOT NULL DEFAULT '[]'::json;

INSERT INTO schema_migrations(version)
SELECT '012_track_approved_media_extractions'
WHERE NOT EXISTS (
    SELECT 1
    FROM schema_migrations
    WHERE version = '012_track_approved_media_extractions'
);

COMMIT;
