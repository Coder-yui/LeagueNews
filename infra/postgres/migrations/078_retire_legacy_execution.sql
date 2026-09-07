BEGIN;

-- Retire only unfinished legacy item executions; evidence, projections,
-- revisions and checkpoint payloads remain available for audit and V3 replay.
-- Deploy with workers stopped. Old decisions must never be replayed into V3.
UPDATE review_tasks SET status = 'superseded', resolved_at = now()
WHERE status = 'pending' AND processing_run_id IN (
    SELECT id FROM processing_runs
    WHERE workflow_type = 'item' AND graph_name IS DISTINCT FROM 'item_processing'
);
UPDATE pipeline_jobs SET status = 'cancelled', next_attempt_at = NULL,
    lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
    completed_at = now(), error_message = 'Legacy execution retired; restart from source evidence in V3'
WHERE status IN ('queued', 'running', 'paused', 'failed') AND processing_run_id IN (
    SELECT id FROM processing_runs
    WHERE workflow_type = 'item' AND graph_name IS DISTINCT FROM 'item_processing'
);
UPDATE pipeline_corrections SET status = 'cancelled', completed_at = now()
WHERE status IN ('queued', 'running') AND id IN (
    SELECT correction_id FROM processing_runs
    WHERE workflow_type = 'item' AND graph_name IS DISTINCT FROM 'item_processing'
);
UPDATE processing_runs SET status = 'failed', outcome = 'system_error',
    completed_at = now(), error_message = 'Legacy execution retired; retry creates a new V3 run from source evidence'
WHERE workflow_type = 'item' AND graph_name IS DISTINCT FROM 'item_processing'
    AND status IN ('running', 'awaiting_review');

INSERT INTO schema_migrations(version) VALUES ('078_retire_legacy_execution')
ON CONFLICT (version) DO NOTHING;
COMMIT;
