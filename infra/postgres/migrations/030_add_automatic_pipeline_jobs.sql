BEGIN;

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL
        REFERENCES raw_items(id) ON DELETE RESTRICT,
    correction_id integer
        REFERENCES pipeline_corrections(id) ON DELETE SET NULL,
    status varchar(30) NOT NULL DEFAULT 'queued',
    current_stage varchar(40) NOT NULL DEFAULT 'relevance',
    processing_run_id integer
        REFERENCES processing_runs(id) ON DELETE SET NULL,
    event_aggregation_run_id integer
        REFERENCES event_aggregation_runs(id) ON DELETE SET NULL,
    last_checkpoint_id integer
        REFERENCES processing_checkpoints(id) ON DELETE SET NULL,
    attempts integer NOT NULL DEFAULT 0,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CONSTRAINT ck_pipeline_jobs_status
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT ck_pipeline_jobs_attempts CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_raw_item_id ON pipeline_jobs(raw_item_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_correction_id ON pipeline_jobs(correction_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_status ON pipeline_jobs(status);
CREATE INDEX IF NOT EXISTS ix_pipeline_jobs_current_stage ON pipeline_jobs(current_stage);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_jobs_active_raw_item
    ON pipeline_jobs(raw_item_id)
    WHERE status IN ('queued', 'running');

INSERT INTO schema_migrations(version)
VALUES ('030_add_automatic_pipeline_jobs')
ON CONFLICT (version) DO NOTHING;

COMMIT;
