BEGIN;

ALTER TABLE pipeline_jobs
    ADD COLUMN next_attempt_at timestamptz;

CREATE INDEX ix_pipeline_jobs_next_attempt_at
    ON pipeline_jobs(next_attempt_at);

INSERT INTO schema_migrations(version)
VALUES ('071_add_pipeline_retry_schedule')
ON CONFLICT (version) DO NOTHING;

COMMIT;
