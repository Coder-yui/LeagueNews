BEGIN;

ALTER TABLE claims
    ADD COLUMN temporal_role varchar(20) NOT NULL DEFAULT 'state',
    ADD COLUMN attribution jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN supersedes_claim_id bigint
        REFERENCES claims(id) ON DELETE SET NULL,
    ADD CONSTRAINT ck_claims_temporal_role
        CHECK (temporal_role IN ('state', 'event', 'prediction'));

CREATE INDEX ix_claims_temporal_role ON claims(temporal_role);
CREATE INDEX ix_claims_supersedes_claim_id ON claims(supersedes_claim_id);

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
                'credibility',
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
                'credibility',
                'importance',
                'claim_gen',
                'item_analysis',
                'event_decision'
            )
        );

INSERT INTO schema_migrations(version)
VALUES ('040_add_timeline_claims_and_claim_stage')
ON CONFLICT (version) DO NOTHING;

COMMIT;
