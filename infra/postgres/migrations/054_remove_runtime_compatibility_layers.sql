BEGIN;

-- Converge stored classifications and workflow stages before narrowing the
-- runtime vocabulary.
UPDATE normalized_items
SET subtopic = 'skin_release'
WHERE subtopic = 'chroma_icon';

UPDATE pipeline_corrections
SET original_event_ids = jsonb_build_array(event_id)
WHERE event_id IS NOT NULL
  AND json_array_length(original_event_ids::json) = 0;

UPDATE pipeline_corrections
SET restart_from_stage = CASE restart_from_stage
    WHEN 'fact_extract' THEN 'fact_classify'
    WHEN 'classify' THEN 'fact_classify'
    WHEN 'item_analysis' THEN 'claim_gen'
    ELSE restart_from_stage
END;

UPDATE processing_checkpoints
SET stage = CASE stage
    WHEN 'fact_extract' THEN 'fact_classify'
    WHEN 'classify' THEN 'fact_classify'
    WHEN 'item_analysis' THEN 'claim_gen'
    ELSE stage
END;

UPDATE review_tasks
SET status = 'superseded',
    resolved_at = coalesce(resolved_at, now())
WHERE status = 'pending'
  AND stage IN ('fact_extract', 'classify');

UPDATE review_tasks
SET stage = CASE stage
    WHEN 'fact_extract' THEN 'fact_classify'
    WHEN 'classify' THEN 'fact_classify'
    WHEN 'item_analysis' THEN 'claim_gen'
    ELSE stage
END;

UPDATE processing_runs
SET current_stage = CASE current_stage
        WHEN 'fact_extract' THEN 'fact_classify'
        WHEN 'classify' THEN 'fact_classify'
        WHEN 'item_analysis' THEN 'claim_gen'
        ELSE current_stage
    END,
    restart_from_stage = CASE restart_from_stage
        WHEN 'fact_extract' THEN 'fact_classify'
        WHEN 'classify' THEN 'fact_classify'
        WHEN 'item_analysis' THEN 'claim_gen'
        ELSE restart_from_stage
    END,
    status = CASE
        WHEN status = 'awaiting_review'
          AND current_stage IN ('fact_extract', 'classify')
        THEN 'failed'
        ELSE status
    END,
    outcome = CASE
        WHEN status = 'awaiting_review'
          AND current_stage IN ('fact_extract', 'classify')
        THEN 'upgrade_retry_required'
        ELSE outcome
    END,
    error_message = CASE
        WHEN status = 'awaiting_review'
          AND current_stage IN ('fact_extract', 'classify')
        THEN 'retry from fact_classify after workflow upgrade'
        ELSE error_message
    END;

UPDATE pipeline_jobs
SET current_stage = CASE current_stage
    WHEN 'fact_extract' THEN 'fact_classify'
    WHEN 'classify' THEN 'fact_classify'
    WHEN 'item_analysis' THEN 'claim_gen'
    ELSE current_stage
END;

ALTER TABLE pipeline_corrections
    DROP CONSTRAINT ck_pipeline_corrections_restart_stage,
    ADD CONSTRAINT ck_pipeline_corrections_restart_stage
        CHECK (
            restart_from_stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'fact_classify',
                'importance',
                'claim_gen',
                'event_decision'
            )
        );

ALTER TABLE processing_checkpoints
    DROP CONSTRAINT ck_processing_checkpoints_stage,
    ADD CONSTRAINT ck_processing_checkpoints_stage
        CHECK (
            stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'fact_classify',
                'importance',
                'claim_gen',
                'event_decision'
            )
        );

-- aggregation_key and membership_role are the sole event identity and
-- membership fields in the current model.
UPDATE events AS target
SET aggregation_key = target.event_key
WHERE target.aggregation_key IS NULL
  AND target.event_key IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM events AS existing
      WHERE existing.id <> target.id
        AND existing.aggregation_key = target.event_key
  );

UPDATE events
SET aggregation_key = concat('legacy:', id, ':', event_key)
WHERE aggregation_key IS NULL
  AND event_key IS NOT NULL;

ALTER TABLE event_messages
    DROP CONSTRAINT IF EXISTS ck_event_messages_relation_type,
    DROP COLUMN relation_type;

ALTER TABLE events
    DROP COLUMN event_key;

DROP INDEX IF EXISTS ix_pipeline_corrections_event_id;
ALTER TABLE pipeline_corrections
    DROP COLUMN event_id;

-- lifecycle_status is the only knowledge-rule state. Relevance rules are no
-- longer consumed, so retire them without deleting their audit records.
ALTER TABLE knowledge_rules
    DROP CONSTRAINT IF EXISTS ck_knowledge_rules_active_lifecycle;

UPDATE knowledge_rules
SET lifecycle_status = 'retired',
    retired_at = coalesce(retired_at, now())
WHERE knowledge_type = 'relevance';

DROP INDEX IF EXISTS ix_knowledge_rules_is_active;
ALTER TABLE knowledge_rules
    DROP COLUMN is_active;

INSERT INTO schema_migrations(version)
VALUES ('054_remove_runtime_compatibility_layers')
ON CONFLICT (version) DO NOTHING;

COMMIT;
