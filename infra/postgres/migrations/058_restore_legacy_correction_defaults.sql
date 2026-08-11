BEGIN;

-- Upgraded databases retain this event-era compatibility column. The current
-- correction model no longer writes it, so keep inserts backward-compatible.
ALTER TABLE pipeline_corrections
    ALTER COLUMN original_event_ids SET DEFAULT '[]'::json;

INSERT INTO schema_migrations(version)
VALUES ('058_restore_legacy_correction_defaults')
ON CONFLICT (version) DO NOTHING;

COMMIT;
