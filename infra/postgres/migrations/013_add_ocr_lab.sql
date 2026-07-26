BEGIN;

ALTER TABLE media_extractions
    DROP CONSTRAINT IF EXISTS uq_media_extraction_task;
ALTER TABLE media_extractions
    ADD COLUMN IF NOT EXISTS processing_config json NOT NULL DEFAULT '{}'::json;

CREATE TABLE IF NOT EXISTS ocr_test_runs (
    id serial PRIMARY KEY,
    media_asset_id integer NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    profile_name varchar(120) NOT NULL,
    parameters json NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'completed',
    raw_text text NOT NULL,
    lines json NOT NULL DEFAULT '[]'::json,
    confidence double precision NOT NULL,
    source_width integer NOT NULL,
    source_height integer NOT NULL,
    processed_width integer NOT NULL,
    processed_height integer NOT NULL,
    overlay_path varchar(1000),
    engine varchar(120) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ocr_test_runs_media_asset_id
    ON ocr_test_runs(media_asset_id);
CREATE INDEX IF NOT EXISTS ix_ocr_test_runs_status
    ON ocr_test_runs(status);

CREATE TABLE IF NOT EXISTS ocr_profiles (
    id serial PRIMARY KEY,
    name varchar(120) NOT NULL,
    parameters json NOT NULL,
    source_test_run_id integer REFERENCES ocr_test_runs(id) ON DELETE SET NULL,
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ocr_profiles_is_active ON ocr_profiles(is_active);

INSERT INTO schema_migrations(version)
SELECT '013_add_ocr_lab'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '013_add_ocr_lab'
);

COMMIT;
