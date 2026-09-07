BEGIN;

-- Phase 2 makes a queued row an explicit execution identity.  Existing rows
-- are retained and classified from their legacy stage; no source evidence or
-- publication history is rewritten.
ALTER TABLE pipeline_jobs
    DROP CONSTRAINT ck_pipeline_jobs_status,
    ADD CONSTRAINT ck_pipeline_jobs_status CHECK (
        status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')
    );

ALTER TABLE pipeline_jobs
    ADD COLUMN job_type varchar(20) NOT NULL DEFAULT 'message',
    ADD COLUMN target_entity_type varchar(40) NOT NULL DEFAULT 'raw_item',
    ADD COLUMN target_entity_id integer,
    ADD COLUMN target_revision integer NOT NULL DEFAULT 1,
    ADD COLUMN workflow_name varchar(80) NOT NULL DEFAULT 'item_processing',
    ADD COLUMN workflow_version varchar(80),
    ADD COLUMN method_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN max_attempts integer NOT NULL DEFAULT 4;

UPDATE pipeline_jobs
SET job_type = 'event',
    target_entity_type = 'normalized_item',
    workflow_name = 'event_aggregation',
    workflow_version = 'v3.0.0-dev2'
WHERE current_stage = 'event_aggregation';

UPDATE pipeline_jobs
SET target_entity_id = raw_item_id,
    target_revision = COALESCE(raw_items.revision, 1)
FROM raw_items
WHERE raw_items.id = pipeline_jobs.raw_item_id
  AND pipeline_jobs.target_entity_type = 'raw_item';

UPDATE pipeline_jobs
SET target_entity_id = normalized_items.id,
    target_revision = normalized_items.current_revision
FROM normalized_items
WHERE normalized_items.raw_item_id = pipeline_jobs.raw_item_id
  AND pipeline_jobs.job_type = 'event';

ALTER TABLE pipeline_jobs
    ADD CONSTRAINT ck_pipeline_jobs_type
        CHECK (job_type IN ('message', 'event')),
    ADD CONSTRAINT ck_pipeline_jobs_target_type
        CHECK (target_entity_type IN ('raw_item', 'normalized_item')),
    ADD CONSTRAINT ck_pipeline_jobs_target_revision
        CHECK (target_revision >= 1),
    ADD CONSTRAINT ck_pipeline_jobs_max_attempts
        CHECK (max_attempts > 0);

DROP INDEX IF EXISTS uq_pipeline_jobs_active_raw_item;
CREATE UNIQUE INDEX uq_pipeline_jobs_active_target
    ON pipeline_jobs(raw_item_id, job_type)
    WHERE status IN ('queued', 'running')
       OR (status = 'failed' AND next_attempt_at IS NOT NULL);

ALTER TABLE processing_runs
    ADD COLUMN method_config jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE media_extractions
    ADD COLUMN processing_run_id integer REFERENCES processing_runs(id) ON DELETE SET NULL,
    ADD COLUMN artifact_scope varchar(20) NOT NULL DEFAULT 'production';

ALTER TABLE media_extractions
    ADD CONSTRAINT ck_media_extractions_artifact_scope
        CHECK (artifact_scope IN ('production', 'shadow', 'experiment'));

CREATE INDEX ix_media_extractions_processing_run_id
    ON media_extractions(processing_run_id);
CREATE INDEX ix_media_extractions_artifact_scope
    ON media_extractions(artifact_scope);

ALTER TABLE review_tasks
    ADD COLUMN command_id varchar(80),
    ADD COLUMN delivery_status varchar(20) NOT NULL DEFAULT 'pending',
    ADD COLUMN delivery_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN delivery_claim_token varchar(80),
    ADD COLUMN delivery_claimed_at timestamptz,
    ADD COLUMN delivery_claim_expires_at timestamptz,
    ADD COLUMN consumed_at timestamptz;

UPDATE review_tasks
SET command_id = 'review-command-' || id::text,
    delivery_status = CASE
        WHEN status IN ('approved', 'rejected', 'superseded') THEN 'recorded'
        ELSE 'pending'
    END
WHERE command_id IS NULL;

ALTER TABLE review_tasks
    ALTER COLUMN command_id SET NOT NULL,
    ADD CONSTRAINT ck_review_tasks_delivery_status
        CHECK (delivery_status IN ('pending', 'recorded', 'consumed')),
    ADD CONSTRAINT ck_review_tasks_delivery_attempts
        CHECK (delivery_attempts >= 0);

CREATE UNIQUE INDEX uq_review_tasks_command_id ON review_tasks(command_id);
CREATE INDEX ix_review_tasks_delivery_claim_expires
    ON review_tasks(delivery_claim_expires_at);

INSERT INTO schema_migrations(version)
VALUES ('077_add_reliable_execution_boundaries')
ON CONFLICT (version) DO NOTHING;

COMMIT;
