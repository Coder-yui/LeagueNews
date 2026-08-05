BEGIN;

DROP INDEX IF EXISTS uq_event_messages_active_normalized_item;

ALTER TABLE event_messages
    ADD COLUMN membership_role varchar(20) NOT NULL DEFAULT 'primary',
    ADD CONSTRAINT ck_event_messages_membership_role
        CHECK (membership_role IN ('primary', 'component', 'cross_ref'));

UPDATE event_messages
SET membership_role = 'primary'
WHERE membership_role IS NULL;

ALTER TABLE events
    ADD COLUMN aggregation_key varchar(255);

CREATE UNIQUE INDEX ix_events_aggregation_key
    ON events(aggregation_key)
    WHERE aggregation_key IS NOT NULL;

ALTER TABLE normalized_items
    ADD COLUMN content_type varchar(40);

ALTER TABLE pipeline_corrections
    DROP CONSTRAINT ck_pipeline_corrections_restart_stage,
    ADD CONSTRAINT ck_pipeline_corrections_restart_stage
        CHECK (
            restart_from_stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'fact_extract',
                'classify',
                'item_analysis',
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
                'fact_extract',
                'classify',
                'item_analysis',
                'event_decision'
            )
        );

INSERT INTO schema_migrations(version)
VALUES ('038_add_multi_membership_and_classification')
ON CONFLICT (version) DO NOTHING;

COMMIT;
