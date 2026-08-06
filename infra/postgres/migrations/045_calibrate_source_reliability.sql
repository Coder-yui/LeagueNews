BEGIN;

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS is_official boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS reliability_score double precision NOT NULL DEFAULT 0.5;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_sources_reliability_score'
    ) THEN
        ALTER TABLE sources
            ADD CONSTRAINT ck_sources_reliability_score
            CHECK (reliability_score >= 0 AND reliability_score <= 1);
    END IF;
END
$$;

UPDATE sources SET
    is_official = connector_type IN ('riot_official', 'tencent_lol')
        OR lower(coalesce(external_key, '')) IN (
            'leagueoflegends', 'lolesports', '5756404150', '5720474518'
        ),
    reliability_score = CASE
        WHEN connector_type IN ('riot_official', 'tencent_lol')
            OR lower(coalesce(external_key, '')) IN (
                'leagueoflegends', 'lolesports', '5756404150', '5720474518'
            ) THEN 1.0
        WHEN lower(name) LIKE ANY (ARRAY['%skinspotlights%', '%spideraxe%']) THEN 0.8
        WHEN lower(name) LIKE ANY (ARRAY['%召唤师park%', '%尧阿尧%', '%riotphroxzon%']) THEN 0.75
        WHEN connector_type IN ('weibo', 'x_twitter', 'baidu_tieba') THEN 0.55
        ELSE 0.5
    END;

UPDATE sources
SET
    is_official = true,
    reliability_score = 1.0
WHERE connector_type = 'x_twitter'
  AND lower(coalesce(external_key, '')) = 'riotphroxzon';

UPDATE sources
SET
    is_official = false,
    reliability_score = 0.6
WHERE (connector_type, external_key) IN (
    ('weibo', '2266865584'),
    ('weibo', '2522098777'),
    ('weibo', '2600241232')
);

UPDATE sources
SET
    is_official = false,
    reliability_score = 0.7
WHERE (connector_type, external_key) IN (
    ('baidu_tieba', '86124184'),
    ('baidu_tieba', '770437943')
);

DROP TABLE IF EXISTS source_reliability_history;

ALTER TABLE normalized_items
    DROP COLUMN IF EXISTS credibility,
    DROP COLUMN IF EXISTS credibility_score,
    DROP COLUMN IF EXISTS credibility_evidence,
    DROP COLUMN IF EXISTS credibility_components,
    DROP COLUMN IF EXISTS credibility_policy_version;

INSERT INTO schema_migrations(version)
VALUES ('045_calibrate_source_reliability')
ON CONFLICT (version) DO NOTHING;

COMMIT;
