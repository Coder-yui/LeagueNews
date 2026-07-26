BEGIN;

ALTER TABLE normalized_items ADD COLUMN source_language varchar(30);
ALTER TABLE normalized_items ADD COLUMN target_language varchar(30) NOT NULL DEFAULT 'zh-CN';
ALTER TABLE normalized_items ADD COLUMN translated_title varchar(500);
ALTER TABLE normalized_items ADD COLUMN translated_text text;
ALTER TABLE normalized_items ADD COLUMN translated_content_blocks json NOT NULL DEFAULT '[]'::json;
ALTER TABLE normalized_items ADD COLUMN translation_status varchar(30) NOT NULL DEFAULT 'pending';
ALTER TABLE normalized_items ADD COLUMN translation_model varchar(120);
CREATE INDEX ix_normalized_items_translation_status ON normalized_items(translation_status);

UPDATE normalized_items n
SET source_language = CASE
        WHEN r.plain_text ~ '[一-龥]' THEN 'zh-CN'
        ELSE COALESCE(r.language, 'en')
    END,
    target_language = 'zh-CN'
FROM raw_items r
WHERE r.id = n.raw_item_id;

UPDATE normalized_items n
SET translated_title = n.normalized_title,
    translated_text = r.plain_text,
    translated_content_blocks = r.content_blocks,
    translation_status = 'not_required',
    translation_model = NULL
FROM raw_items r
WHERE r.id = n.raw_item_id
  AND n.source_language = 'zh-CN';

INSERT INTO schema_migrations(version) VALUES ('004_add_translation_fields');

COMMIT;
