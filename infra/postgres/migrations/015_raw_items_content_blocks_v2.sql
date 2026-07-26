BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'raw_items' AND column_name = 'title'
    ) THEN
        ALTER TABLE raw_items RENAME COLUMN title TO native_title;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'raw_items' AND column_name = 'source_url'
    ) THEN
        ALTER TABLE raw_items RENAME COLUMN source_url TO canonical_url;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'raw_items' AND column_name = 'author'
    ) THEN
        ALTER TABLE raw_items RENAME COLUMN author TO author_name;
    END IF;
END $$;

ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS content_kind varchar(30) NOT NULL DEFAULT 'post';
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS author_handle varchar(255);
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS author_external_id varchar(255);
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS author_url varchar(1000);
ALTER TABLE raw_items
    ADD COLUMN IF NOT EXISTS content_schema_version integer NOT NULL DEFAULT 1;

UPDATE raw_items r
SET
    native_title = CASE
        WHEN s.connector_type IN ('x_twitter', 'weibo') THEN NULL
        ELSE r.native_title
    END,
    content_kind = CASE
        WHEN s.connector_type IN ('riot_official', 'tencent_lol') THEN 'article'
        WHEN s.connector_type = 'baidu_tieba' THEN 'thread'
        WHEN s.connector_type = 'manual' THEN 'manual'
        ELSE 'post'
    END,
    author_external_id = CASE
        WHEN s.connector_type IN ('x_twitter', 'weibo', 'baidu_tieba')
            THEN s.external_key
        ELSE NULL
    END,
    author_url = CASE
        WHEN s.connector_type = 'x_twitter' AND s.external_key IS NOT NULL
            THEN 'https://x.com/' || trim(leading '@' from s.external_key)
        WHEN s.connector_type = 'weibo' AND s.external_key IS NOT NULL
            THEN 'https://weibo.com/u/' || s.external_key
        ELSE NULL
    END
FROM sources s
WHERE s.id = r.source_id;

UPDATE raw_items
SET content_blocks = (
    SELECT COALESCE(
        json_agg(
            CASE
                WHEN block->>'type' = 'video' THEN
                    (block::jsonb - 'type')
                    || jsonb_build_object(
                        'id', 'b' || lpad(ordinality::text, 4, '0'),
                        'type', 'embed',
                        'embed_kind', 'video'
                    )
                WHEN block->>'type' = 'embed' THEN
                    block::jsonb
                    || jsonb_build_object(
                        'id', 'b' || lpad(ordinality::text, 4, '0'),
                        'source_url',
                        CASE
                            WHEN COALESCE(block->>'source_url', '') ~ '^https?://'
                                THEN block->>'source_url'
                            ELSE raw_items.canonical_url
                        END,
                        'text',
                        CASE
                            WHEN COALESCE(block->>'source_url', '') ~ '^https?://'
                                THEN block->>'text'
                            WHEN lower(COALESCE(block->>'text', '')) ~ '投票|poll|vote'
                                THEN '投票'
                            WHEN lower(COALESCE(block->>'text', '')) ~ '视频|video'
                                THEN '视频'
                            ELSE '媒体内容'
                        END,
                        'embed_kind',
                        CASE
                            WHEN lower(COALESCE(block->>'text', '')) ~ '投票|poll|vote'
                                THEN 'poll'
                            WHEN lower(COALESCE(block->>'text', '')) ~ '视频|video'
                                THEN 'video'
                            WHEN lower(COALESCE(block->>'text', '')) ~ '原微博|引用|quoted'
                                THEN 'quoted_post'
                            ELSE 'external_link'
                        END
                    )
                WHEN block->>'type' = 'list' THEN
                    (block::jsonb - 'text')
                    || jsonb_build_object(
                        'id', 'b' || lpad(ordinality::text, 4, '0'),
                        'items', jsonb_build_array(block->>'text'),
                        'ordered', false
                    )
                WHEN block->>'type' = 'heading' THEN
                    block::jsonb
                    || jsonb_build_object(
                        'id', 'b' || lpad(ordinality::text, 4, '0'),
                        'level', 2
                    )
                ELSE
                    block::jsonb
                    || jsonb_build_object(
                        'id', 'b' || lpad(ordinality::text, 4, '0')
                    )
            END
            ORDER BY ordinality
        ),
        '[]'::json
    )
    FROM json_array_elements(raw_items.content_blocks) WITH ORDINALITY AS entry(block, ordinality)
)
WHERE content_schema_version = 1;

UPDATE raw_items SET content_schema_version = 2 WHERE content_schema_version = 1;
ALTER TABLE raw_items ALTER COLUMN content_schema_version SET DEFAULT 2;

CREATE TABLE IF NOT EXISTS raw_item_source_payloads (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL UNIQUE REFERENCES raw_items(id) ON DELETE CASCADE,
    provider varchar(50) NOT NULL,
    payload json NOT NULL DEFAULT '{}'::json,
    captured_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_raw_item_source_payloads_raw_item_id
    ON raw_item_source_payloads(raw_item_id);
CREATE INDEX IF NOT EXISTS ix_raw_item_source_payloads_provider
    ON raw_item_source_payloads(provider);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'raw_items' AND column_name = 'raw_payload'
    ) THEN
        EXECUTE $migration$
            INSERT INTO raw_item_source_payloads(raw_item_id, provider, payload, captured_at)
            SELECT
                r.id,
                COALESCE(NULLIF(r.raw_payload->>'provider', ''), s.connector_type),
                r.raw_payload,
                r.ingested_at
            FROM raw_items r
            JOIN sources s ON s.id = r.source_id
            ON CONFLICT (raw_item_id) DO NOTHING
        $migration$;
    END IF;
END $$;

DROP INDEX IF EXISTS ix_raw_items_status;
ALTER TABLE raw_items DROP COLUMN IF EXISTS plain_text;
ALTER TABLE raw_items DROP COLUMN IF EXISTS raw_payload;
ALTER TABLE raw_items DROP COLUMN IF EXISTS status;

INSERT INTO schema_migrations(version)
VALUES ('015_raw_items_content_blocks_v2')
ON CONFLICT (version) DO NOTHING;

COMMIT;
