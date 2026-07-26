BEGIN;

ALTER TABLE ocr_test_runs
    ADD COLUMN IF NOT EXISTS table_overlay_path varchar(1000);
ALTER TABLE ocr_test_runs
    ADD COLUMN IF NOT EXISTS table_data json NOT NULL DEFAULT '{}'::json;
ALTER TABLE ocr_test_runs
    ADD COLUMN IF NOT EXISTS structure_confidence double precision;

INSERT INTO schema_migrations(version)
SELECT '014_add_patch_table_structure'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '014_add_patch_table_structure'
);

COMMIT;
