BEGIN;

ALTER TABLE events
    ADD COLUMN event_type varchar(40) NOT NULL DEFAULT 'other',
    ADD COLUMN lifecycle_status varchar(40) NOT NULL DEFAULT 'developing',
    ADD COLUMN credibility_status varchar(40) NOT NULL DEFAULT 'unverified',
    ADD COLUMN credibility_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN importance_score double precision NOT NULL DEFAULT 0,
    ADD COLUMN importance_evidence json NOT NULL DEFAULT '[]'::json,
    ADD COLUMN latest_development text NOT NULL DEFAULT '',
    ADD COLUMN independent_source_count integer NOT NULL DEFAULT 0,
    ADD COLUMN official_source_count integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT ck_events_credibility_score
        CHECK (credibility_score >= 0 AND credibility_score <= 1),
    ADD CONSTRAINT ck_events_importance_score
        CHECK (importance_score >= 0 AND importance_score <= 1),
    ADD CONSTRAINT ck_events_independent_source_count
        CHECK (independent_source_count >= 0),
    ADD CONSTRAINT ck_events_official_source_count
        CHECK (official_source_count >= 0);

ALTER TABLE event_messages
    ADD COLUMN evidence_stance varchar(20) NOT NULL DEFAULT 'supports',
    ADD COLUMN independence_key varchar(255),
    ADD COLUMN is_official_confirmation boolean NOT NULL DEFAULT false,
    ADD COLUMN is_significant_update boolean NOT NULL DEFAULT true,
    ADD CONSTRAINT ck_event_messages_evidence_stance
        CHECK (evidence_stance IN ('supports', 'contradicts', 'context'));

UPDATE event_messages em
SET independence_key = 'source:' || raw.source_id::text
FROM normalized_items ni
JOIN raw_items raw ON raw.id = ni.raw_item_id
WHERE ni.id = em.normalized_item_id;

UPDATE event_messages em
SET is_official_confirmation = true
FROM normalized_items ni
WHERE ni.id = em.normalized_item_id
  AND ni.credibility = 'official';

WITH event_metrics AS (
    SELECT
        em.event_id,
        max(ni.importance_score) AS importance_score,
        count(DISTINCT raw.source_id) AS independent_source_count,
        count(DISTINCT raw.source_id)
            FILTER (WHERE em.is_official_confirmation) AS official_source_count
    FROM event_messages em
    JOIN normalized_items ni ON ni.id = em.normalized_item_id
    JOIN raw_items raw ON raw.id = ni.raw_item_id
    GROUP BY em.event_id
)
UPDATE events e
SET
    event_type = CASE
        WHEN e.event_key LIKE 'patch:%' THEN 'patch'
        WHEN e.event_key LIKE 'match:%' THEN 'match'
        WHEN e.event_key LIKE 'transfer:%' THEN 'transfer'
        WHEN e.category LIKE '%转会%' THEN 'transfer'
        WHEN e.category LIKE '%赛事%' OR e.category LIKE '%赛果%' THEN 'match'
        ELSE 'other'
    END,
    importance_score = metrics.importance_score,
    importance_evidence = json_build_array(
        '由现有成员消息的重要性最高值回填；后续更新使用事件级规则'
    ),
    independent_source_count = metrics.independent_source_count,
    official_source_count = metrics.official_source_count,
    credibility_status = CASE
        WHEN metrics.official_source_count > 0 THEN 'official_confirmed'
        WHEN metrics.independent_source_count > 1 THEN 'multi_source_confirmed'
        ELSE 'single_source'
    END,
    credibility_score = CASE
        WHEN metrics.official_source_count > 0 THEN 1
        ELSE LEAST(
            0.99,
            1 - power(
                1 - LEAST(
                    0.85,
                    (
                        SELECT max(ni2.credibility_score)
                        FROM event_messages em2
                        JOIN normalized_items ni2
                            ON ni2.id = em2.normalized_item_id
                        WHERE em2.event_id = e.id
                    )
                ),
                metrics.independent_source_count
            )
        )
    END,
    lifecycle_status = CASE
        WHEN metrics.official_source_count > 0 THEN 'confirmed'
        ELSE 'developing'
    END,
    latest_development = COALESCE(
        (
            SELECT er.change_note
            FROM event_revisions er
            WHERE er.event_id = e.id
            ORDER BY er.revision DESC
            LIMIT 1
        ),
        ''
    )
FROM event_metrics metrics
WHERE metrics.event_id = e.id;

CREATE INDEX ix_events_event_type ON events(event_type);
CREATE INDEX ix_events_lifecycle_status ON events(lifecycle_status);
CREATE INDEX ix_events_credibility_status ON events(credibility_status);
CREATE INDEX ix_events_importance_score ON events(importance_score);
CREATE INDEX ix_event_messages_independence_key
    ON event_messages(event_id, independence_key);

INSERT INTO schema_migrations(version)
VALUES ('028_add_event_editorial_metrics')
ON CONFLICT (version) DO NOTHING;

COMMIT;
