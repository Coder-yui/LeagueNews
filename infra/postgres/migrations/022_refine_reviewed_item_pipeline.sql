BEGIN;

ALTER TABLE processing_runs
    ADD COLUMN IF NOT EXISTS supersedes_run_id integer
        REFERENCES processing_runs(id) ON DELETE SET NULL;
ALTER TABLE processing_runs
    ADD COLUMN IF NOT EXISTS outcome varchar(40);

CREATE INDEX IF NOT EXISTS ix_processing_runs_supersedes_run_id
    ON processing_runs(supersedes_run_id);
CREATE INDEX IF NOT EXISTS ix_processing_runs_outcome
    ON processing_runs(outcome);
CREATE UNIQUE INDEX IF NOT EXISTS uq_processing_runs_active_item
    ON processing_runs(raw_item_id)
    WHERE workflow_type = 'item' AND status IN ('running', 'awaiting_review');
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_tasks_pending_run
    ON review_tasks(processing_run_id)
    WHERE status = 'pending';

ALTER TABLE normalized_items
    ADD COLUMN IF NOT EXISTS credibility_score double precision;
ALTER TABLE normalized_items
    ADD COLUMN IF NOT EXISTS credibility_evidence json NOT NULL DEFAULT '[]'::json;
UPDATE normalized_items
SET credibility_score = CASE credibility
    WHEN 'official' THEN 1.0
    WHEN 'corroborated' THEN 0.8
    WHEN 'unverified' THEN 0.5
    WHEN 'rumor' THEN 0.2
    ELSE 0.5
END
WHERE credibility_score IS NULL;
ALTER TABLE normalized_items
    ALTER COLUMN credibility_score SET NOT NULL;
CREATE INDEX IF NOT EXISTS ix_normalized_items_credibility_score
    ON normalized_items(credibility_score);

CREATE TABLE IF NOT EXISTS normalized_item_media_extractions (
    normalized_item_id integer NOT NULL
        REFERENCES normalized_items(id) ON DELETE CASCADE,
    media_extraction_id integer NOT NULL
        REFERENCES media_extractions(id) ON DELETE RESTRICT,
    translated_structured_data json NOT NULL DEFAULT '{}'::json,
    translation_status varchar(30) NOT NULL DEFAULT 'pending',
    translation_model varchar(120),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (normalized_item_id, media_extraction_id)
);
CREATE INDEX IF NOT EXISTS ix_normalized_item_media_translation_status
    ON normalized_item_media_extractions(translation_status);

INSERT INTO normalized_item_media_extractions (
    normalized_item_id,
    media_extraction_id,
    translated_structured_data,
    translation_status,
    translation_model
)
SELECT
    n.id,
    extraction_id.value::integer,
    m.structured_data,
    n.translation_status,
    n.translation_model
FROM normalized_items n
CROSS JOIN LATERAL json_array_elements_text(n.approved_media_extraction_ids)
    AS extraction_id(value)
JOIN media_extractions m ON m.id = extraction_id.value::integer
ON CONFLICT (normalized_item_id, media_extraction_id) DO NOTHING;

ALTER TABLE normalized_items
    DROP COLUMN IF EXISTS approved_media_extraction_ids;
DROP INDEX IF EXISTS ix_normalized_items_event_status;
ALTER TABLE normalized_items
    DROP COLUMN IF EXISTS event_status;

INSERT INTO schema_migrations(version)
VALUES ('022_refine_reviewed_item_pipeline')
ON CONFLICT (version) DO NOTHING;

COMMIT;
