BEGIN;

-- PipelineJob identity is the execution target, not the RawItem that happens
-- to have caused the work.  Keep raw_item_id as provenance/ownership data.
UPDATE pipeline_jobs
SET target_entity_id = raw_item_id
WHERE target_entity_id IS NULL
  AND target_entity_type = 'raw_item';

-- An event job without its NormalizedItem target cannot be safely replayed.
-- Retire that executable row while preserving the job and its provenance.
UPDATE pipeline_jobs
SET status = 'cancelled',
    next_attempt_at = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    worker_id = NULL,
    completed_at = COALESCE(completed_at, now()),
    error_message = COALESCE(
        error_message,
        'Event execution retired because its NormalizedItem target is unavailable'
    )
WHERE target_entity_type = 'normalized_item'
  AND target_entity_id IS NULL;

UPDATE pipeline_jobs
SET target_entity_id = raw_item_id
WHERE target_entity_id IS NULL;

UPDATE pipeline_jobs
SET current_stage = 'load_message'
WHERE job_type = 'event'
  AND current_stage NOT IN (
      'load_message', 'minimal_filter', 'candidate_retrieval',
      'semantic_decision', 'apply_membership', 'refresh_projection'
  );
DROP INDEX IF EXISTS uq_pipeline_jobs_active_target;
ALTER TABLE pipeline_jobs
    ALTER COLUMN target_entity_id SET NOT NULL;
CREATE UNIQUE INDEX uq_pipeline_jobs_active_execution_identity
    ON pipeline_jobs(
        workflow_name,
        target_entity_type,
        target_entity_id,
        target_revision
    )
    WHERE status IN ('queued', 'running')
       OR (status = 'failed' AND next_attempt_at IS NOT NULL);

-- EventAggregationRun now records the same execution model as the graph.  A
-- stage checkpoint remains audit data in decision_draft, but graph identity,
-- thread identity and method configuration are first-class run metadata.
ALTER TABLE event_aggregation_runs
    ADD COLUMN graph_name varchar(80),
    ADD COLUMN graph_version varchar(80),
    ADD COLUMN state_version integer,
    ADD COLUMN thread_id varchar(255),
    ADD COLUMN method_config jsonb NOT NULL DEFAULT '{}'::jsonb;

UPDATE event_aggregation_runs
SET graph_name = 'event_aggregation',
    graph_version = COALESCE(decision_draft->>'graph_version', 'v3.0.0-dev2'),
    state_version = 1,
    method_config = COALESCE(decision_draft->'method_config', '{}'::jsonb),
    thread_id = 'event_aggregation:'
        || COALESCE(decision_draft->>'graph_version', 'v3.0.0-dev2')
        || ':production:live:run:' || id::text
        || ':item:' || normalized_item_id::text
        || ':revision:' || normalized_item_revision::text
WHERE graph_name IS NULL;

UPDATE event_aggregation_runs
SET decision_draft = decision_draft
    - 'graph_version'
    - 'method_config'
    - 'review_mode';

-- Preserve the audit rows while bringing any unfinished pre-V3 run into the
-- current stage vocabulary before tightening the constraint.
UPDATE event_aggregation_runs
SET current_stage = CASE current_stage
    WHEN 'minimal_filter' THEN 'load_message'
    WHEN 'model_decision' THEN 'semantic_decision'
    WHEN 'apply_membership' THEN 'apply_membership'
    ELSE current_stage
END
WHERE current_stage IN ('minimal_filter', 'model_decision', 'apply_membership');

ALTER TABLE event_aggregation_runs
    ALTER COLUMN graph_name SET NOT NULL,
    ALTER COLUMN graph_version SET NOT NULL,
    ALTER COLUMN state_version SET NOT NULL,
    ALTER COLUMN thread_id SET NOT NULL,
    DROP CONSTRAINT ck_event_runs_stage,
    ADD CONSTRAINT ck_event_runs_stage CHECK (
        current_stage IN (
            'load_message', 'minimal_filter', 'candidate_retrieval',
            'semantic_decision', 'apply_membership', 'refresh_projection'
        )
    );

CREATE UNIQUE INDEX uq_event_aggregation_runs_thread_id
    ON event_aggregation_runs(thread_id);

INSERT INTO schema_migrations(version)
VALUES ('079_finalize_v3_execution_identity')
ON CONFLICT (version) DO NOTHING;

COMMIT;
