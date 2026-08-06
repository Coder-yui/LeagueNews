BEGIN;

ALTER TABLE events
    ADD COLUMN IF NOT EXISTS supporting_source_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS contradicting_source_count integer NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_events_supporting_source_count'
    ) THEN
        ALTER TABLE events
            ADD CONSTRAINT ck_events_supporting_source_count
            CHECK (supporting_source_count >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_events_contradicting_source_count'
    ) THEN
        ALTER TABLE events
            ADD CONSTRAINT ck_events_contradicting_source_count
            CHECK (contradicting_source_count >= 0);
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'event_messages'
          AND column_name = 'is_official_confirmation'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'event_messages'
          AND column_name = 'is_official_evidence'
    ) THEN
        ALTER TABLE event_messages
            RENAME COLUMN is_official_confirmation TO is_official_evidence;
    END IF;
END
$$;

ALTER TABLE event_messages
    ADD COLUMN IF NOT EXISTS source_reliability_snapshot
        double precision NOT NULL DEFAULT 0.5,
    ADD COLUMN IF NOT EXISTS timeline_note text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS update_kind varchar(30) NOT NULL DEFAULT 'new_fact';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_event_messages_source_reliability_snapshot'
    ) THEN
        ALTER TABLE event_messages
            ADD CONSTRAINT ck_event_messages_source_reliability_snapshot
            CHECK (
                source_reliability_snapshot >= 0
                AND source_reliability_snapshot <= 1
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_event_messages_update_kind'
    ) THEN
        ALTER TABLE event_messages
            ADD CONSTRAINT ck_event_messages_update_kind
            CHECK (update_kind IN (
                'new_fact',
                'confirmation',
                'refutation',
                'correction',
                'context',
                'duplicate_evidence'
            ));
    END IF;
END
$$;

UPDATE event_messages em SET
    source_reliability_snapshot = s.reliability_score,
    is_official_evidence = s.is_official
        AND em.evidence_stance IN ('supports', 'contradicts')
        AND NOT EXISTS (
            SELECT 1 FROM normalized_items ni
            JOIN raw_items ri ON ri.id = ni.raw_item_id
            CROSS JOIN LATERAL jsonb_array_elements(ri.content_blocks::jsonb) block
            WHERE ni.id = em.normalized_item_id
              AND block->>'type' = 'embed'
              AND block->>'embed_kind' = 'quoted_post'
        )
FROM normalized_items ni
JOIN raw_items ri ON ri.id = ni.raw_item_id
JOIN sources s ON s.id = ri.source_id
WHERE ni.id = em.normalized_item_id;

WITH membership_sources AS (
    SELECT
        em.event_id,
        em.normalized_item_id,
        ri.source_id,
        ni.summary,
        ni.normalized_title,
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(ri.content_blocks::jsonb) block
            WHERE block->>'type' = 'embed'
              AND block->>'embed_kind' = 'quoted_post'
        ) AS has_quoted_post,
        (
            SELECT block->>'source_url'
            FROM jsonb_array_elements(ri.content_blocks::jsonb) block
            WHERE block->>'type' = 'embed'
              AND block->>'embed_kind' = 'quoted_post'
              AND NULLIF(block->>'source_url', '') IS NOT NULL
            LIMIT 1
        ) AS quoted_source_url
    FROM event_messages em
    JOIN normalized_items ni ON ni.id = em.normalized_item_id
    JOIN raw_items ri ON ri.id = ni.raw_item_id
)
UPDATE event_messages em SET
    independence_key = CASE
        WHEN ms.quoted_source_url IS NOT NULL THEN
            'upstream:' || rtrim(
                regexp_replace(
                    regexp_replace(
                        lower(ms.quoted_source_url),
                        '^https?://(www\.)?',
                        '',
                        'i'
                    ),
                    '[?#].*$',
                    ''
                ),
                '/'
            )
        WHEN ms.has_quoted_post THEN NULL
        ELSE 'source:' || ms.source_id::text
    END,
    timeline_note = COALESCE(NULLIF(ms.summary, ''), ms.normalized_title),
    update_kind = CASE
        WHEN em.evidence_stance = 'context' THEN 'context'
        ELSE 'new_fact'
    END,
    is_significant_update = em.evidence_stance <> 'context'
FROM membership_sources ms
WHERE ms.event_id = em.event_id
  AND ms.normalized_item_id = em.normalized_item_id;

WITH eligible_evidence AS (
    SELECT
        em.event_id,
        em.independence_key,
        em.evidence_stance,
        em.is_official_evidence,
        em.source_reliability_snapshot
    FROM event_messages em
    JOIN normalized_items ni ON ni.id = em.normalized_item_id
    WHERE em.membership_status = 'active'
      AND ni.publication_status = 'published'
      AND em.evidence_stance IN ('supports', 'contradicts')
      AND em.independence_key IS NOT NULL
),
all_counts AS (
    SELECT
        event_id,
        count(DISTINCT independence_key)
            FILTER (WHERE evidence_stance = 'supports') AS supporting_count,
        count(DISTINCT independence_key)
            FILTER (WHERE evidence_stance = 'contradicts') AS contradicting_count,
        count(DISTINCT independence_key) AS independent_count,
        count(DISTINCT independence_key)
            FILTER (WHERE is_official_evidence) AS official_count,
        bool_or(
            is_official_evidence AND evidence_stance = 'supports'
        ) AS official_support,
        bool_or(
            is_official_evidence AND evidence_stance = 'contradicts'
        ) AS official_contradiction
    FROM eligible_evidence
    GROUP BY event_id
),
nonofficial_by_key AS (
    SELECT
        event_id,
        independence_key,
        evidence_stance,
        max(source_reliability_snapshot) AS reliability_score
    FROM eligible_evidence
    WHERE NOT is_official_evidence
    GROUP BY event_id, independence_key, evidence_stance
),
nonofficial_counts AS (
    SELECT
        event_id,
        count(*) FILTER (
            WHERE evidence_stance = 'supports'
        ) AS supporting_count,
        count(*) FILTER (
            WHERE evidence_stance = 'contradicts'
        ) AS contradicting_count,
        max(reliability_score) FILTER (
            WHERE evidence_stance = 'supports'
        ) AS base_score
    FROM nonofficial_by_key
    GROUP BY event_id
),
metrics AS (
    SELECT
        e.id AS event_id,
        coalesce(ac.supporting_count, 0) AS supporting_count,
        coalesce(ac.contradicting_count, 0) AS contradicting_count,
        coalesce(ac.independent_count, 0) AS independent_count,
        coalesce(ac.official_count, 0) AS official_count,
        coalesce(ac.official_support, false) AS official_support,
        coalesce(ac.official_contradiction, false) AS official_contradiction,
        coalesce(nc.supporting_count, 0) AS nonofficial_supporting_count,
        coalesce(nc.contradicting_count, 0) AS nonofficial_contradicting_count,
        coalesce(nc.base_score, 0) AS base_score
    FROM events e
    LEFT JOIN all_counts ac ON ac.event_id = e.id
    LEFT JOIN nonofficial_counts nc ON nc.event_id = e.id
)
UPDATE events e SET
    supporting_source_count = metrics.supporting_count,
    contradicting_source_count = metrics.contradicting_count,
    independent_source_count = metrics.independent_count,
    official_source_count = metrics.official_count,
    credibility_status = CASE
        WHEN metrics.official_support
             AND metrics.official_contradiction THEN 'disputed'
        WHEN metrics.official_support THEN 'official_confirmed'
        WHEN metrics.official_contradiction THEN 'officially_refuted'
        WHEN metrics.nonofficial_supporting_count > 0
             AND metrics.nonofficial_contradicting_count > 0 THEN 'disputed'
        WHEN metrics.nonofficial_supporting_count >= 2 THEN 'multi_source_supported'
        WHEN metrics.nonofficial_supporting_count = 1 THEN 'single_source'
        ELSE 'unverified'
    END,
    credibility_score = CASE
        WHEN metrics.official_support
             AND metrics.official_contradiction THEN 0.5
        WHEN metrics.official_support THEN 1.0
        WHEN metrics.official_contradiction THEN 0.0
        WHEN metrics.nonofficial_supporting_count > 0
             AND metrics.nonofficial_contradicting_count > 0 THEN 0.5
        WHEN metrics.nonofficial_supporting_count > 0 THEN least(
            0.9,
            metrics.base_score
                + 0.1 * least(metrics.nonofficial_supporting_count - 1, 3)
        )
        ELSE 0.0
    END,
    lifecycle_status = CASE
        WHEN metrics.official_support
             AND metrics.official_contradiction THEN 'disputed'
        WHEN metrics.official_support
             AND e.lifecycle_status IN (
                 'unconfirmed',
                 'developing',
                 'disputed',
                 'expired_unconfirmed'
             ) THEN 'confirmed'
        WHEN metrics.official_contradiction THEN 'officially_refuted'
        WHEN metrics.nonofficial_supporting_count > 0
             AND metrics.nonofficial_contradicting_count > 0 THEN 'disputed'
        ELSE e.lifecycle_status
    END
FROM metrics
WHERE metrics.event_id = e.id;

WITH eligible_importance AS (
    SELECT
        em.event_id,
        ni.importance_score,
        em.is_significant_update
    FROM event_messages em
    JOIN normalized_items ni ON ni.id = em.normalized_item_id
    WHERE em.membership_status = 'active'
      AND ni.publication_status = 'published'
      AND em.evidence_stance <> 'context'
      AND em.update_kind NOT IN ('context', 'duplicate_evidence')
),
importance_metrics AS (
    SELECT
        e.id AS event_id,
        coalesce(
            max(ei.importance_score) FILTER (WHERE ei.is_significant_update),
            max(ei.importance_score),
            0
        ) AS importance_score
    FROM events e
    LEFT JOIN eligible_importance ei ON ei.event_id = e.id
    GROUP BY e.id
)
UPDATE events e SET
    importance_score = im.importance_score,
    importance_evidence = json_build_array(
        '迁移时按活动、已发布、非 context 成员的重要性最高值重建'
    )
FROM importance_metrics im
WHERE im.event_id = e.id;

ALTER TABLE pipeline_corrections
    ADD COLUMN IF NOT EXISTS original_event_ids jsonb NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO schema_migrations(version)
VALUES ('047_add_event_evidence_projection')
ON CONFLICT (version) DO NOTHING;

COMMIT;
