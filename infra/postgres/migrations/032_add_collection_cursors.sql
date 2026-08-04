BEGIN;

ALTER TABLE source_collection_schedules
    ADD COLUMN collection_cursor json NOT NULL DEFAULT '{}'::json,
    ADD COLUMN overlap_minutes integer NOT NULL DEFAULT 10,
    ADD CONSTRAINT ck_source_collection_schedules_overlap
        CHECK (overlap_minutes >= 0 AND overlap_minutes <= 1440);

ALTER TABLE connector_runs
    ADD COLUMN candidate_count integer NOT NULL DEFAULT 0,
    ADD COLUMN truncated boolean NOT NULL DEFAULT false,
    ADD COLUMN cursor_used json NOT NULL DEFAULT '{}'::json,
    ADD COLUMN next_cursor json NOT NULL DEFAULT '{}'::json;

INSERT INTO schema_migrations(version)
VALUES ('032_add_collection_cursors')
ON CONFLICT (version) DO NOTHING;

COMMIT;
