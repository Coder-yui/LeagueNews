BEGIN;

-- Event V2 reads importance from the current NormalizedItem projection. The
-- retired identity key and copied importance snapshot no longer participate in
-- runtime behavior, so remove them instead of preserving parallel contracts.
DROP INDEX IF EXISTS ix_events_aggregation_key;
ALTER TABLE events DROP COLUMN IF EXISTS aggregation_key;
ALTER TABLE event_mentions DROP COLUMN IF EXISTS impact_snapshot;

-- The retired ontology/digest experiment has no model, route, service, or
-- workflow consumer in the current publication pipeline.
DROP TABLE IF EXISTS digest_revisions;
DROP TABLE IF EXISTS digests;
DROP TABLE IF EXISTS claims;

UPDATE media_assets
SET visibility = 'published'
WHERE visibility = 'legacy_public';
ALTER TABLE media_assets DROP CONSTRAINT IF EXISTS ck_media_assets_visibility;
ALTER TABLE media_assets
    ADD CONSTRAINT ck_media_assets_visibility
    CHECK (visibility IN ('private', 'published'));

ALTER TABLE normalized_items
    DROP COLUMN IF EXISTS product_scope,
    DROP COLUMN IF EXISTS primary_topic,
    DROP COLUMN IF EXISTS secondary_topics,
    DROP COLUMN IF EXISTS subtopic,
    DROP COLUMN IF EXISTS source_kind,
    DROP COLUMN IF EXISTS information_stage,
    DROP COLUMN IF EXISTS ontology_version;

ALTER TABLE normalized_items
    ADD CONSTRAINT ck_normalized_items_importance_score
    CHECK (importance_score >= 0 AND importance_score <= 1);

UPDATE event_aggregation_runs
SET current_stage = 'minimal_filter'
WHERE current_stage = 'admission';

UPDATE processing_runs
SET current_stage = CASE current_stage
        WHEN 'fact_classify' THEN 'message_analysis'
        WHEN 'claim_gen' THEN 'importance'
        WHEN 'event_decision' THEN 'importance'
        ELSE current_stage
    END,
    restart_from_stage = CASE restart_from_stage
        WHEN 'fact_classify' THEN 'message_analysis'
        WHEN 'claim_gen' THEN 'importance'
        WHEN 'event_decision' THEN 'importance'
        ELSE restart_from_stage
    END,
    outcome = CASE outcome
        WHEN 'upgrade_retry_required' THEN 'system_error'
        ELSE outcome
    END;

UPDATE review_tasks
SET stage = CASE stage
    WHEN 'fact_classify' THEN 'message_analysis'
    WHEN 'claim_gen' THEN 'importance'
    WHEN 'event_decision' THEN 'importance'
    ELSE stage
END;

UPDATE processing_checkpoints
SET stage = CASE stage
    WHEN 'fact_classify' THEN 'message_analysis'
    WHEN 'claim_gen' THEN 'importance'
    WHEN 'event_decision' THEN 'importance'
    ELSE stage
END;

ALTER TABLE processing_runs
    ADD CONSTRAINT ck_processing_runs_status
    CHECK (status IN (
        'running', 'awaiting_review', 'completed', 'rejected', 'failed', 'superseded'
    )),
    ADD CONSTRAINT ck_processing_runs_outcome
    CHECK (outcome IS NULL OR outcome IN (
        'approved', 'irrelevant', 'review_rejected', 'system_error',
        'correction_requested', 'raw_item_superseded'
    )),
    ADD CONSTRAINT ck_processing_runs_stage
    CHECK (current_stage IN (
        'relevance', 'image_ocr', 'translation', 'message_analysis', 'importance'
    )),
    ADD CONSTRAINT ck_processing_runs_execution_mode
    CHECK (execution_mode IN ('manual', 'automatic'));

ALTER TABLE review_tasks
    ADD CONSTRAINT ck_review_tasks_status
    CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    ADD CONSTRAINT ck_review_tasks_stage
    CHECK (stage IN (
        'relevance', 'image_ocr', 'translation', 'message_analysis', 'importance'
    )),
    ADD CONSTRAINT ck_review_tasks_decision_source
    CHECK (decision_source IN ('manual', 'automatic'));

ALTER TABLE event_aggregation_runs
    ADD CONSTRAINT ck_event_runs_status
    CHECK (status IN ('running', 'completed', 'failed')),
    ADD CONSTRAINT ck_event_runs_outcome
    CHECK (outcome IS NULL OR outcome IN (
        'skipped_by_minimal_filter', 'applied', 'ignored', 'model_error', 'apply_error'
    )),
    ADD CONSTRAINT ck_event_runs_stage
    CHECK (current_stage IN ('minimal_filter', 'model_decision', 'apply_membership')),
    ADD CONSTRAINT ck_event_runs_admission
    CHECK (admission_decision IS NULL OR admission_decision IN ('process', 'skip'));

DROP INDEX IF EXISTS uq_event_aggregation_runs_active_item;
CREATE UNIQUE INDEX uq_event_aggregation_runs_active_item
    ON event_aggregation_runs(normalized_item_id)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS ix_events_first_seen_at ON events(first_seen_at);

ALTER TABLE events
    ALTER COLUMN aggregation_policy_version
        SET DEFAULT 'event-aggregation-v4-semantic-membership',
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'event-importance-v4-normalized-item-projection';
ALTER TABLE event_mentions
    ALTER COLUMN aggregation_policy_version
        SET DEFAULT 'event-aggregation-v4-semantic-membership';
ALTER TABLE event_aggregation_runs
    ALTER COLUMN aggregation_policy_version
        SET DEFAULT 'event-aggregation-v4-semantic-membership',
    ALTER COLUMN current_stage SET DEFAULT 'minimal_filter';

INSERT INTO schema_migrations(version)
VALUES ('067_remove_runtime_compatibility_and_enforce_state')
ON CONFLICT (version) DO NOTHING;

COMMIT;
