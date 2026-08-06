BEGIN;

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
                'fact_classify',
                'importance',
                'claim_gen',
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
                'fact_classify',
                'importance',
                'claim_gen',
                'item_analysis',
                'event_decision'
            )
        );

INSERT INTO schema_migrations(version)
VALUES ('043_merge_fact_classify_review_stage')
ON CONFLICT (version) DO NOTHING;

COMMIT;
