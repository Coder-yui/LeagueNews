BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version varchar(100) PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE raw_items RENAME COLUMN url TO source_url;
ALTER TABLE raw_items RENAME COLUMN content TO plain_text;
ALTER TABLE raw_items ADD COLUMN author varchar(255);
ALTER TABLE raw_items ADD COLUMN language varchar(30);
ALTER TABLE raw_items ADD COLUMN content_blocks json NOT NULL DEFAULT '[]'::json;
ALTER TABLE raw_items ADD COLUMN raw_payload json NOT NULL DEFAULT '{}'::json;
ALTER TABLE raw_items ADD COLUMN content_hash varchar(64);
CREATE INDEX ix_raw_items_content_hash ON raw_items(content_hash);

UPDATE raw_items
SET content_blocks = json_build_array(json_build_object('type', 'paragraph', 'text', plain_text)),
    raw_payload = json_build_object(
        'migrated_from', 'v1',
        'title', title,
        'source_url', source_url
    ),
    content_hash = encode(sha256(convert_to(plain_text, 'UTF8')), 'hex');

CREATE TABLE media_assets (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL REFERENCES raw_items(id) ON DELETE CASCADE,
    block_index integer NOT NULL,
    source_url varchar(1000),
    storage_path varchar(1000),
    mime_type varchar(120),
    sha256 varchar(64),
    width integer,
    height integer,
    alt_text varchar(500),
    caption text,
    ocr_text text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_media_assets_raw_item_id ON media_assets(raw_item_id);
CREATE INDEX ix_media_assets_sha256 ON media_assets(sha256);

CREATE TABLE normalized_items (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL UNIQUE REFERENCES raw_items(id) ON DELETE CASCADE,
    normalized_title varchar(500) NOT NULL,
    normalized_text text NOT NULL,
    summary text NOT NULL,
    category varchar(60) NOT NULL,
    entities json NOT NULL DEFAULT '[]'::json,
    importance_score double precision NOT NULL,
    credibility varchar(30) NOT NULL,
    language varchar(30),
    analysis_model varchar(120) NOT NULL,
    analysis_version varchar(30) NOT NULL DEFAULT 'v2',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ix_normalized_items_raw_item_id ON normalized_items(raw_item_id);
CREATE INDEX ix_normalized_items_category ON normalized_items(category);
CREATE INDEX ix_normalized_items_importance_score ON normalized_items(importance_score);
CREATE INDEX ix_normalized_items_credibility ON normalized_items(credibility);

INSERT INTO normalized_items (
    raw_item_id,
    normalized_title,
    normalized_text,
    summary,
    category,
    entities,
    importance_score,
    credibility,
    language,
    analysis_model,
    analysis_version,
    created_at,
    updated_at
)
SELECT
    e.raw_item_id,
    e.title,
    r.plain_text,
    e.summary,
    e.category,
    e.entities,
    e.importance_score,
    e.credibility,
    r.language,
    'legacy-migrated',
    'v1',
    e.created_at,
    e.created_at
FROM news_events e
JOIN raw_items r ON r.id = e.raw_item_id;

CREATE TABLE event_items (
    event_id integer NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
    normalized_item_id integer NOT NULL REFERENCES normalized_items(id) ON DELETE CASCADE,
    relation_type varchar(30) NOT NULL DEFAULT 'primary',
    is_primary boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, normalized_item_id)
);

INSERT INTO event_items (event_id, normalized_item_id, relation_type, is_primary)
SELECT e.id, n.id, 'primary', true
FROM news_events e
JOIN normalized_items n ON n.raw_item_id = e.raw_item_id;

ALTER TABLE news_events DROP CONSTRAINT news_events_raw_item_id_fkey;
DROP INDEX ix_news_events_raw_item_id;
ALTER TABLE news_events DROP COLUMN raw_item_id;

INSERT INTO media_assets (
    raw_item_id, block_index, storage_path, mime_type, alt_text, caption
)
SELECT id, 1, '/media/patch-26-14-preview.jpg', 'image/jpeg',
       'League of Legends Patch Preview 26.14', '26.14 版本改动预览图'
FROM raw_items
WHERE title = 'Patch 26.14 Full Preview!';

UPDATE raw_items
SET content_blocks = (
    content_blocks::jsonb || jsonb_build_array(jsonb_build_object(
        'type', 'image',
        'storage_path', '/media/patch-26-14-preview.jpg',
        'alt_text', 'League of Legends Patch Preview 26.14',
        'caption', '26.14 版本改动预览图'
    ))
)::json
WHERE title = 'Patch 26.14 Full Preview!';

INSERT INTO media_assets (
    raw_item_id, block_index, storage_path, mime_type, alt_text, caption
)
SELECT id, 1, '/media/haidou-tournament-cover.png', 'image/png',
       '首届海斗大赛宣传封面', '首届海斗大赛宣传封面'
FROM raw_items
WHERE title LIKE '首届海斗大赛%';

INSERT INTO media_assets (
    raw_item_id, block_index, storage_path, mime_type, alt_text, caption
)
SELECT id, 2, '/media/haidou-tournament-details.png', 'image/png',
       '海斗大赛全民赛道赛程与晋级信息', '全民赛道赛程与晋级信息'
FROM raw_items
WHERE title LIKE '首届海斗大赛%';

UPDATE raw_items
SET content_blocks = (
    content_blocks::jsonb
    || jsonb_build_array(jsonb_build_object(
        'type', 'image',
        'storage_path', '/media/haidou-tournament-cover.png',
        'alt_text', '首届海斗大赛宣传封面',
        'caption', '首届海斗大赛宣传封面'
    ))
    || jsonb_build_array(jsonb_build_object(
        'type', 'image',
        'storage_path', '/media/haidou-tournament-details.png',
        'alt_text', '海斗大赛全民赛道赛程与晋级信息',
        'caption', '全民赛道赛程与晋级信息'
    ))
)::json
WHERE title LIKE '首届海斗大赛%';

INSERT INTO schema_migrations(version) VALUES ('002_content_pipeline_v2');

COMMIT;
