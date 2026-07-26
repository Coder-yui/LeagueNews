BEGIN;

UPDATE raw_items
SET content_blocks = (
    SELECT json_agg(
        CASE
            WHEN block->>'type' = 'embed'
                AND COALESCE(block->>'source_url', '') !~ '^https?://'
            THEN block::jsonb || jsonb_build_object(
                'source_url', raw_items.canonical_url,
                'text',
                CASE
                    WHEN block->>'embed_kind' = 'poll' THEN '投票'
                    WHEN block->>'embed_kind' = 'video' THEN '视频'
                    ELSE '媒体内容'
                END
            )
            ELSE block::jsonb
        END
        ORDER BY ordinality
    )
    FROM json_array_elements(raw_items.content_blocks)
        WITH ORDINALITY AS entry(block, ordinality)
)
WHERE EXISTS (
    SELECT 1
    FROM json_array_elements(raw_items.content_blocks) AS block
    WHERE block->>'type' = 'embed'
      AND COALESCE(block->>'source_url', '') !~ '^https?://'
);

UPDATE raw_items
SET content_hash = encode(sha256(convert_to(content_blocks::text, 'UTF8')), 'hex')
WHERE content_schema_version = 2;

INSERT INTO schema_migrations(version)
VALUES ('016_normalize_legacy_embed_urls')
ON CONFLICT (version) DO NOTHING;

COMMIT;
