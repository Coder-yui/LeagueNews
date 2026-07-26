BEGIN;

CREATE TABLE media_extractions (
    id serial PRIMARY KEY,
    media_asset_id integer NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    task_type varchar(60) NOT NULL,
    provider varchar(120) NOT NULL,
    ocr_engine varchar(120) NOT NULL,
    structuring_model varchar(120) NOT NULL,
    schema_version varchar(30) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'pending',
    raw_ocr_text text NOT NULL,
    ocr_lines json NOT NULL DEFAULT '[]'::json,
    structured_data json NOT NULL DEFAULT '{}'::json,
    confidence double precision,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_media_extraction_task UNIQUE (media_asset_id, task_type, schema_version)
);

CREATE INDEX ix_media_extractions_media_asset_id ON media_extractions(media_asset_id);
CREATE INDEX ix_media_extractions_status ON media_extractions(status);
CREATE INDEX ix_media_extractions_task_type ON media_extractions(task_type);

INSERT INTO schema_migrations(version) VALUES ('006_add_media_extractions');

COMMIT;
