BEGIN;

CREATE TABLE IF NOT EXISTS event_aggregation_runs (
    id serial PRIMARY KEY,
    normalized_item_id integer NOT NULL
        REFERENCES normalized_items(id) ON DELETE RESTRICT,
    supersedes_run_id integer
        REFERENCES event_aggregation_runs(id) ON DELETE SET NULL,
    status varchar(40) NOT NULL DEFAULT 'running',
    outcome varchar(40),
    current_stage varchar(40) NOT NULL DEFAULT 'event_decision',
    candidate_snapshot json NOT NULL DEFAULT '[]'::json,
    decision_draft json NOT NULL DEFAULT '{}'::json,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_event_aggregation_runs_normalized_item_id
    ON event_aggregation_runs(normalized_item_id);
CREATE INDEX IF NOT EXISTS ix_event_aggregation_runs_supersedes_run_id
    ON event_aggregation_runs(supersedes_run_id);
CREATE INDEX IF NOT EXISTS ix_event_aggregation_runs_status ON event_aggregation_runs(status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_aggregation_runs_active_item
    ON event_aggregation_runs(normalized_item_id)
    WHERE status IN ('running', 'awaiting_review');

CREATE TABLE IF NOT EXISTS event_review_tasks (
    id serial PRIMARY KEY,
    event_aggregation_run_id integer NOT NULL
        REFERENCES event_aggregation_runs(id) ON DELETE CASCADE,
    status varchar(30) NOT NULL DEFAULT 'pending',
    proposal json NOT NULL DEFAULT '{}'::json,
    feedback json NOT NULL DEFAULT '{}'::json,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_event_review_tasks_run_id
    ON event_review_tasks(event_aggregation_run_id);
CREATE INDEX IF NOT EXISTS ix_event_review_tasks_status ON event_review_tasks(status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_review_tasks_pending_run
    ON event_review_tasks(event_aggregation_run_id)
    WHERE status = 'pending';

ALTER TABLE knowledge_rules
    ADD COLUMN IF NOT EXISTS source_event_review_id integer
        REFERENCES event_review_tasks(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_knowledge_rules_source_event_review_id
    ON knowledge_rules(source_event_review_id);

INSERT INTO schema_migrations(version)
VALUES ('027_add_event_review_workflow')
ON CONFLICT (version) DO NOTHING;

COMMIT;
