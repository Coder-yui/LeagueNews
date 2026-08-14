BEGIN;

DROP INDEX IF EXISTS uq_pipeline_jobs_active_raw_item;

CREATE UNIQUE INDEX uq_pipeline_jobs_active_raw_item
    ON pipeline_jobs(raw_item_id)
    WHERE status IN ('queued', 'running')
       OR (status = 'failed' AND next_attempt_at IS NOT NULL);

INSERT INTO schema_migrations(version)
VALUES ('072_include_retry_pending_pipeline_jobs')
ON CONFLICT (version) DO NOTHING;

COMMIT;
