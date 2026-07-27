BEGIN;

ALTER TABLE normalized_items
    ADD COLUMN current_revision integer NOT NULL DEFAULT 1,
    ADD COLUMN publication_status varchar(30) NOT NULL DEFAULT 'published',
    ADD COLUMN withdrawn_at timestamptz,
    ADD COLUMN withdrawal_reason text,
    ADD CONSTRAINT ck_normalized_items_current_revision_positive
        CHECK (current_revision >= 1),
    ADD CONSTRAINT ck_normalized_items_publication_status
        CHECK (publication_status IN ('published', 'withdrawn'));

CREATE INDEX ix_normalized_items_publication_status
    ON normalized_items(publication_status);

CREATE TABLE IF NOT EXISTS normalized_item_revisions (
    id serial PRIMARY KEY,
    normalized_item_id integer NOT NULL
        REFERENCES normalized_items(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    snapshot json NOT NULL DEFAULT '{}'::json,
    processing_run_id integer
        REFERENCES processing_runs(id) ON DELETE SET NULL,
    change_note text NOT NULL DEFAULT 'published',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_normalized_item_revisions_item_revision
        UNIQUE (normalized_item_id, revision),
    CONSTRAINT ck_normalized_item_revisions_revision_positive
        CHECK (revision >= 1)
);

CREATE INDEX IF NOT EXISTS ix_normalized_item_revisions_normalized_item_id
    ON normalized_item_revisions(normalized_item_id);
CREATE INDEX IF NOT EXISTS ix_normalized_item_revisions_processing_run_id
    ON normalized_item_revisions(processing_run_id);

INSERT INTO normalized_item_revisions (
    normalized_item_id,
    revision,
    snapshot,
    change_note,
    created_at
)
SELECT
    ni.id,
    1,
    json_build_object(
        'normalized_title', ni.normalized_title,
        'normalized_text', ni.normalized_text,
        'summary', ni.summary,
        'category', ni.category,
        'entities', ni.entities,
        'importance_score', ni.importance_score,
        'credibility', ni.credibility,
        'credibility_score', ni.credibility_score,
        'credibility_evidence', ni.credibility_evidence,
        'language', ni.language,
        'source_language', ni.source_language,
        'target_language', ni.target_language,
        'translated_title', ni.translated_title,
        'translated_text', ni.translated_text,
        'translated_content_blocks', ni.translated_content_blocks,
        'translation_status', ni.translation_status,
        'translation_model', ni.translation_model,
        'analysis_model', ni.analysis_model,
        'analysis_version', ni.analysis_version,
        'approved_media_extraction_ids', COALESCE(
            (
                SELECT json_agg(link.media_extraction_id ORDER BY link.media_extraction_id)
                FROM normalized_item_media_extractions link
                WHERE link.normalized_item_id = ni.id
            ),
            '[]'::json
        )
    ),
    '029 migration: preserve existing published projection',
    ni.created_at
FROM normalized_items ni;

CREATE TABLE IF NOT EXISTS pipeline_corrections (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL
        REFERENCES raw_items(id) ON DELETE RESTRICT,
    normalized_item_id integer
        REFERENCES normalized_items(id) ON DELETE RESTRICT,
    event_id integer
        REFERENCES events(id) ON DELETE SET NULL,
    source_processing_run_id integer
        REFERENCES processing_runs(id) ON DELETE SET NULL,
    source_event_run_id integer
        REFERENCES event_aggregation_runs(id) ON DELETE SET NULL,
    checkpoint_id integer,
    restart_from_stage varchar(40) NOT NULL,
    resume_mode varchar(20) NOT NULL,
    reason text NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'requested',
    error_message text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT ck_pipeline_corrections_restart_stage
        CHECK (
            restart_from_stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'item_analysis',
                'event_decision'
            )
        ),
    CONSTRAINT ck_pipeline_corrections_resume_mode
        CHECK (resume_mode IN ('manual', 'automatic')),
    CONSTRAINT ck_pipeline_corrections_status
        CHECK (status IN ('requested', 'running', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_raw_item_id
    ON pipeline_corrections(raw_item_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_normalized_item_id
    ON pipeline_corrections(normalized_item_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_event_id
    ON pipeline_corrections(event_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_restart_from_stage
    ON pipeline_corrections(restart_from_stage);
CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_resume_mode
    ON pipeline_corrections(resume_mode);
CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_status
    ON pipeline_corrections(status);

CREATE TABLE IF NOT EXISTS processing_checkpoints (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL
        REFERENCES raw_items(id) ON DELETE RESTRICT,
    normalized_item_id integer
        REFERENCES normalized_items(id) ON DELETE SET NULL,
    processing_run_id integer
        REFERENCES processing_runs(id) ON DELETE SET NULL,
    event_aggregation_run_id integer
        REFERENCES event_aggregation_runs(id) ON DELETE SET NULL,
    correction_id integer
        REFERENCES pipeline_corrections(id) ON DELETE SET NULL,
    stage varchar(40) NOT NULL,
    output_snapshot json NOT NULL DEFAULT '{}'::json,
    artifact_references json NOT NULL DEFAULT '{}'::json,
    knowledge_snapshot json NOT NULL DEFAULT '{}'::json,
    model_name varchar(120),
    decision_source varchar(20) NOT NULL DEFAULT 'manual',
    created_at timestamptz NOT NULL DEFAULT now(),
    invalidated_at timestamptz,
    invalidation_reason text,
    CONSTRAINT ck_processing_checkpoints_stage
        CHECK (
            stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'item_analysis',
                'event_decision'
            )
        ),
    CONSTRAINT ck_processing_checkpoints_decision_source
        CHECK (decision_source IN ('manual', 'automatic', 'system'))
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_pipeline_corrections_checkpoint_id'
    ) THEN
        ALTER TABLE pipeline_corrections
            ADD CONSTRAINT fk_pipeline_corrections_checkpoint_id
            FOREIGN KEY (checkpoint_id)
            REFERENCES processing_checkpoints(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_pipeline_corrections_checkpoint_id
    ON pipeline_corrections(checkpoint_id);
CREATE INDEX IF NOT EXISTS ix_processing_checkpoints_raw_item_id
    ON processing_checkpoints(raw_item_id);
CREATE INDEX IF NOT EXISTS ix_processing_checkpoints_normalized_item_id
    ON processing_checkpoints(normalized_item_id);
CREATE INDEX IF NOT EXISTS ix_processing_checkpoints_processing_run_id
    ON processing_checkpoints(processing_run_id);
CREATE INDEX IF NOT EXISTS ix_processing_checkpoints_event_aggregation_run_id
    ON processing_checkpoints(event_aggregation_run_id);
CREATE INDEX IF NOT EXISTS ix_processing_checkpoints_correction_id
    ON processing_checkpoints(correction_id);
CREATE INDEX IF NOT EXISTS ix_processing_checkpoints_stage
    ON processing_checkpoints(stage);

ALTER TABLE processing_runs
    ADD COLUMN execution_mode varchar(20) NOT NULL DEFAULT 'manual',
    ADD COLUMN correction_id integer
        REFERENCES pipeline_corrections(id) ON DELETE SET NULL,
    ADD COLUMN restart_from_stage varchar(40);
CREATE INDEX ix_processing_runs_execution_mode
    ON processing_runs(execution_mode);
CREATE INDEX ix_processing_runs_correction_id
    ON processing_runs(correction_id);

ALTER TABLE review_tasks
    ADD COLUMN decision_source varchar(20) NOT NULL DEFAULT 'manual',
    ADD COLUMN policy_version varchar(80);
CREATE INDEX ix_review_tasks_decision_source
    ON review_tasks(decision_source);

ALTER TABLE event_aggregation_runs
    ADD COLUMN execution_mode varchar(20) NOT NULL DEFAULT 'manual',
    ADD COLUMN correction_id integer
        REFERENCES pipeline_corrections(id) ON DELETE SET NULL,
    ADD COLUMN restart_from_stage varchar(40);
CREATE INDEX ix_event_aggregation_runs_execution_mode
    ON event_aggregation_runs(execution_mode);
CREATE INDEX ix_event_aggregation_runs_correction_id
    ON event_aggregation_runs(correction_id);

ALTER TABLE event_review_tasks
    ADD COLUMN decision_source varchar(20) NOT NULL DEFAULT 'manual',
    ADD COLUMN policy_version varchar(80);
CREATE INDEX ix_event_review_tasks_decision_source
    ON event_review_tasks(decision_source);

ALTER TABLE event_messages
    DROP CONSTRAINT uq_event_messages_normalized_item,
    ADD COLUMN membership_status varchar(20) NOT NULL DEFAULT 'active',
    ADD COLUMN withdrawn_at timestamptz,
    ADD COLUMN withdrawal_reason text,
    ADD COLUMN source_correction_id integer
        REFERENCES pipeline_corrections(id) ON DELETE SET NULL,
    ADD CONSTRAINT ck_event_messages_membership_status
        CHECK (membership_status IN ('active', 'withdrawn'));

CREATE UNIQUE INDEX uq_event_messages_active_normalized_item
    ON event_messages(normalized_item_id)
    WHERE membership_status = 'active';
CREATE INDEX ix_event_messages_membership_status
    ON event_messages(membership_status);
CREATE INDEX ix_event_messages_source_correction_id
    ON event_messages(source_correction_id);

INSERT INTO schema_migrations(version)
VALUES ('029_add_pipeline_corrections')
ON CONFLICT (version) DO NOTHING;

COMMIT;
