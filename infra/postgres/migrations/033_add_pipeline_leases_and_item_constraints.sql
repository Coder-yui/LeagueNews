BEGIN;

ALTER TABLE pipeline_jobs
    ADD COLUMN worker_id varchar(160),
    ADD COLUMN lease_token varchar(64),
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN heartbeat_at timestamptz,
    ADD COLUMN recovery_count integer NOT NULL DEFAULT 0,
    ADD COLUMN recovery_provenance json NOT NULL DEFAULT '[]'::json,
    ADD CONSTRAINT ck_pipeline_jobs_recovery_count CHECK (recovery_count >= 0);

CREATE INDEX ix_pipeline_jobs_worker_id ON pipeline_jobs(worker_id);
CREATE INDEX ix_pipeline_jobs_lease_token ON pipeline_jobs(lease_token);
CREATE INDEX ix_pipeline_jobs_lease_expires_at ON pipeline_jobs(lease_expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_processing_runs_active_raw_item
    ON processing_runs(raw_item_id)
    WHERE workflow_type = 'item'
      AND status IN ('running', 'awaiting_review');

CREATE UNIQUE INDEX IF NOT EXISTS uq_review_tasks_pending_run
    ON review_tasks(processing_run_id)
    WHERE status = 'pending';

INSERT INTO schema_migrations(version)
VALUES ('033_add_pipeline_leases_and_item_constraints')
ON CONFLICT (version) DO NOTHING;

COMMIT;
