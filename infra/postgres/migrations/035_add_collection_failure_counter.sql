BEGIN;

ALTER TABLE source_collection_schedules
    ADD COLUMN consecutive_failures integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT ck_source_collection_schedules_consecutive_failures
        CHECK (consecutive_failures >= 0);

INSERT INTO schema_migrations(version)
VALUES ('035_add_collection_failure_counter')
ON CONFLICT (version) DO NOTHING;

COMMIT;
