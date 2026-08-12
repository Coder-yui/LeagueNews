BEGIN;

ALTER TABLE events
    DROP COLUMN IF EXISTS unresolved_points;

INSERT INTO schema_migrations(version)
VALUES ('065_remove_event_unresolved_points')
ON CONFLICT (version) DO NOTHING;

COMMIT;
