BEGIN;

CREATE TABLE source_reliability_history (
    id bigserial PRIMARY KEY,
    source_id bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    confirmed_count integer NOT NULL DEFAULT 0,
    refuted_count integer NOT NULL DEFAULT 0,
    alpha double precision NOT NULL,
    beta double precision NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_reliability_history_source UNIQUE (source_id),
    CONSTRAINT ck_source_reliability_confirmed_count
        CHECK (confirmed_count >= 0),
    CONSTRAINT ck_source_reliability_refuted_count
        CHECK (refuted_count >= 0),
    CONSTRAINT ck_source_reliability_alpha CHECK (alpha > 0),
    CONSTRAINT ck_source_reliability_beta CHECK (beta >= 0)
);

CREATE INDEX ix_source_reliability_history_updated_at
    ON source_reliability_history(updated_at);

INSERT INTO source_reliability_history (source_id, alpha, beta)
SELECT
    id,
    CASE
        WHEN connector_type IN ('riot_official', 'tencent_lol')
            OR lower(coalesce(external_key, '')) IN (
                'leagueoflegends',
                'lolesports',
                '5756404150',
                '5720474518'
            )
            THEN 10.0
        WHEN lower(name) LIKE ANY (
            ARRAY['%skinspotlights%', '%spideraxe%']
        )
            THEN 8.0
        WHEN lower(name) LIKE ANY (
            ARRAY['%召唤师park%', '%尧阿尧%', '%riotphroxzon%']
        )
            THEN 7.5
        WHEN connector_type IN ('weibo', 'x_twitter', 'baidu_tieba')
            THEN 5.5
        ELSE 3.5
    END,
    CASE
        WHEN connector_type IN ('riot_official', 'tencent_lol')
            OR lower(coalesce(external_key, '')) IN (
                'leagueoflegends',
                'lolesports',
                '5756404150',
                '5720474518'
            )
            THEN 0.0
        WHEN lower(name) LIKE ANY (
            ARRAY['%skinspotlights%', '%spideraxe%']
        )
            THEN 2.0
        WHEN lower(name) LIKE ANY (
            ARRAY['%召唤师park%', '%尧阿尧%', '%riotphroxzon%']
        )
            THEN 2.5
        WHEN connector_type IN ('weibo', 'x_twitter', 'baidu_tieba')
            THEN 4.5
        ELSE 6.5
    END
FROM sources
ON CONFLICT (source_id) DO NOTHING;

ALTER TABLE normalized_items
    ALTER COLUMN credibility_policy_version
        SET DEFAULT 'credibility-v2-four-factor-beta',
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'importance-v2-five-dimensions';

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
                'item_analysis',
                'event_decision'
            )
        );

INSERT INTO schema_migrations(version)
VALUES ('039_add_source_reliability_and_scoring_stages')
ON CONFLICT (version) DO NOTHING;

COMMIT;
