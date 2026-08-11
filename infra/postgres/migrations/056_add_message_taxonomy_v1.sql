BEGIN;

ALTER TABLE normalized_items
    ADD COLUMN products jsonb NOT NULL DEFAULT '["unknown"]'::jsonb,
    ADD COLUMN message_type varchar(80) NOT NULL DEFAULT 'unknown',
    ADD COLUMN topics jsonb NOT NULL DEFAULT '["unknown"]'::jsonb,
    ADD COLUMN classification_version varchar(40)
        NOT NULL DEFAULT 'message-taxonomy-v1';

CREATE INDEX ix_normalized_items_message_type
    ON normalized_items(message_type);
CREATE INDEX ix_normalized_items_products_gin
    ON normalized_items USING gin(products);
CREATE INDEX ix_normalized_items_topics_gin
    ON normalized_items USING gin(topics);

ALTER TABLE pipeline_corrections
    DROP CONSTRAINT ck_pipeline_corrections_restart_stage,
    ADD CONSTRAINT ck_pipeline_corrections_restart_stage
        CHECK (
            restart_from_stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'message_analysis',
                'importance'
            )
        ) NOT VALID;

ALTER TABLE processing_checkpoints
    DROP CONSTRAINT ck_processing_checkpoints_stage,
    ADD CONSTRAINT ck_processing_checkpoints_stage
        CHECK (
            stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'message_analysis',
                'importance'
            )
        ) NOT VALID;

INSERT INTO schema_migrations(version)
VALUES ('056_add_message_taxonomy_v1')
ON CONFLICT (version) DO NOTHING;

COMMIT;
