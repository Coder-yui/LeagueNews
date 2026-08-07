BEGIN;

-- Fresh databases are created from ORM metadata and then mark the historical
-- ledger complete. Reapply the final source calibration here so both fresh and
-- upgraded databases converge on the same baseline.
UPDATE sources SET
    is_official = connector_type IN ('riot_official', 'tencent_lol')
        OR lower(coalesce(external_key, '')) IN (
            'leagueoflegends', 'lolesports', '5756404150', '5720474518',
            'riotphroxzon'
        ),
    reliability_score = CASE
        WHEN connector_type IN ('riot_official', 'tencent_lol')
            OR lower(coalesce(external_key, '')) IN (
                'leagueoflegends', 'lolesports', '5756404150', '5720474518',
                'riotphroxzon'
            ) THEN 1.0
        WHEN lower(name) LIKE ANY (ARRAY['%skinspotlights%', '%spideraxe%']) THEN 0.8
        WHEN lower(name) LIKE ANY (ARRAY['%召唤师park%', '%尧阿尧%']) THEN 0.6
        WHEN connector_type = 'baidu_tieba' THEN 0.7
        WHEN connector_type IN ('weibo', 'x_twitter') THEN 0.55
        ELSE 0.5
    END;

ALTER TABLE normalized_items
    ADD COLUMN source_kind varchar(30) NOT NULL DEFAULT 'unknown',
    ADD COLUMN information_stage varchar(30) NOT NULL DEFAULT 'update',
    ADD COLUMN content_form varchar(30) NOT NULL DEFAULT 'original',
    ADD COLUMN subtopic varchar(40) NOT NULL DEFAULT 'other',
    ADD COLUMN product_scope varchar(40) NOT NULL DEFAULT 'uncertain',
    ADD COLUMN priority_score double precision NOT NULL DEFAULT 0.5,
    ADD COLUMN priority_calculation json NOT NULL DEFAULT '{}'::json;

ALTER TABLE normalized_items
    ALTER COLUMN ontology_version SET DEFAULT 'lol-news-v2',
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'importance-v4-intrinsic-priority';

DROP INDEX IF EXISTS ix_normalized_items_category;
ALTER TABLE normalized_items
    DROP COLUMN category,
    DROP COLUMN content_type;

CREATE INDEX ix_normalized_items_source_kind ON normalized_items(source_kind);
CREATE INDEX ix_normalized_items_information_stage ON normalized_items(information_stage);
CREATE INDEX ix_normalized_items_subtopic ON normalized_items(subtopic);
CREATE INDEX ix_normalized_items_product_scope ON normalized_items(product_scope);
CREATE INDEX ix_normalized_items_priority_score ON normalized_items(priority_score);

ALTER TABLE normalized_items
    ADD CONSTRAINT ck_normalized_items_priority_score
        CHECK (priority_score >= 0 AND priority_score <= 1);

ALTER TABLE events
    ADD COLUMN event_kind varchar(50) NOT NULL DEFAULT 'other',
    ADD COLUMN aggregation_strategy varchar(30) NOT NULL DEFAULT 'singleton',
    ADD COLUMN product_scope varchar(40) NOT NULL DEFAULT 'uncertain',
    ADD COLUMN importance_dimensions json NOT NULL DEFAULT '{}'::json,
    ADD COLUMN importance_policy_version varchar(80)
        NOT NULL DEFAULT 'event-importance-v1';

DROP INDEX IF EXISTS ix_events_category;
DROP INDEX IF EXISTS ix_events_event_type;
ALTER TABLE events
    DROP COLUMN category,
    DROP COLUMN event_type;

CREATE INDEX ix_events_event_kind ON events(event_kind);
CREATE INDEX ix_events_product_scope ON events(product_scope);

ALTER TABLE event_messages
    ADD COLUMN importance_contribution double precision NOT NULL DEFAULT 0,
    ADD COLUMN importance_contribution_evidence json NOT NULL DEFAULT '[]'::json,
    ADD CONSTRAINT ck_event_messages_importance_contribution
        CHECK (importance_contribution >= 0 AND importance_contribution <= 1);

INSERT INTO schema_migrations(version)
VALUES ('048_add_processing_ontology_v2')
ON CONFLICT (version) DO NOTHING;

COMMIT;
