BEGIN;

-- A stale run for an older NormalizedItem revision must not block the current
-- revision. Keep one active execution per item revision while retaining all
-- historical runs for audit and replay.
DROP INDEX IF EXISTS uq_event_aggregation_runs_active_item;
CREATE UNIQUE INDEX uq_event_aggregation_runs_active_item
    ON event_aggregation_runs(normalized_item_id, normalized_item_revision)
    WHERE status = 'running';

INSERT INTO schema_migrations(version)
VALUES ('069_fence_event_runs_by_revision')
ON CONFLICT (version) DO NOTHING;

COMMIT;
