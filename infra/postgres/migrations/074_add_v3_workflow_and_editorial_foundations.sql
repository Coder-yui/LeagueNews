BEGIN;

ALTER TABLE processing_runs
    ADD COLUMN graph_name varchar(80),
    ADD COLUMN graph_version varchar(80),
    ADD COLUMN state_version integer,
    ADD COLUMN thread_id varchar(255);

CREATE UNIQUE INDEX uq_processing_runs_thread_id
    ON processing_runs(thread_id)
    WHERE thread_id IS NOT NULL;

ALTER TABLE processing_runs
    DROP CONSTRAINT ck_processing_runs_stage,
    ADD CONSTRAINT ck_processing_runs_stage CHECK (
        current_stage IN (
            'evidence', 'relevance', 'image_ocr', 'media', 'translation',
            'message_analysis', 'importance', 'evidence_gate', 'publication'
        )
    );

ALTER TABLE review_tasks
    DROP CONSTRAINT ck_review_tasks_stage,
    ADD CONSTRAINT ck_review_tasks_stage CHECK (
        stage IN (
            'relevance', 'image_ocr', 'media', 'translation',
            'message_analysis', 'importance', 'evidence_gate'
        )
    );

ALTER TABLE pipeline_corrections
    DROP CONSTRAINT ck_pipeline_corrections_restart_stage,
    ADD CONSTRAINT ck_pipeline_corrections_restart_stage CHECK (
        restart_from_stage IN (
            'evidence', 'relevance', 'image_ocr', 'media', 'translation',
            'message_analysis', 'importance', 'evidence_gate', 'publication'
        )
    );

ALTER TABLE processing_checkpoints
    ADD COLUMN graph_name varchar(80),
    ADD COLUMN graph_version varchar(80),
    ADD COLUMN state_version integer,
    ADD COLUMN evidence_fingerprint varchar(64),
    ADD COLUMN idempotency_key varchar(255),
    ADD COLUMN upstream_checkpoint_ids json NOT NULL DEFAULT '{}'::json;

CREATE INDEX ix_processing_checkpoints_graph_name
    ON processing_checkpoints(graph_name);
CREATE UNIQUE INDEX uq_processing_checkpoints_idempotency_key
    ON processing_checkpoints(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE processing_checkpoints
    DROP CONSTRAINT ck_processing_checkpoints_stage,
    ADD CONSTRAINT ck_processing_checkpoints_stage CHECK (
        stage IN (
            'evidence', 'relevance', 'image_ocr', 'media', 'translation',
            'message_analysis', 'importance', 'evidence_gate', 'publication'
        )
    );

ALTER TABLE normalized_items
    ADD COLUMN manual_override_fields json NOT NULL DEFAULT '[]'::json;

ALTER TABLE normalized_item_revisions
    ADD COLUMN revision_source varchar(20) NOT NULL DEFAULT 'workflow',
    ADD COLUMN editor_id varchar(160),
    ADD COLUMN idempotency_key varchar(255),
    ADD CONSTRAINT ck_normalized_item_revisions_source CHECK (
        revision_source IN ('workflow', 'manual')
    );

CREATE UNIQUE INDEX uq_normalized_item_revisions_idempotency_key
    ON normalized_item_revisions(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE events
    ADD COLUMN manual_override_fields json NOT NULL DEFAULT '[]'::json;

ALTER TABLE event_revisions
    ADD COLUMN revision_source varchar(20) NOT NULL DEFAULT 'workflow',
    ADD COLUMN editor_id varchar(160),
    ADD COLUMN idempotency_key varchar(255),
    ADD CONSTRAINT ck_event_revisions_source CHECK (
        revision_source IN ('workflow', 'manual')
    );

CREATE UNIQUE INDEX uq_event_revisions_idempotency_key
    ON event_revisions(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES ('074_add_v3_workflow_and_editorial_foundations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
