BEGIN;

WITH rebuilt AS (
    SELECT
        r.id,
        string_agg(block.value->>'text', E'\n\n' ORDER BY block.ordinality) AS plain_text
    FROM raw_items r
    CROSS JOIN LATERAL json_array_elements(r.content_blocks)
        WITH ORDINALITY AS block(value, ordinality)
    WHERE r.status = 'pending'
      AND r.source_url IS NOT NULL
      AND r.plain_text = r.source_url
      AND block.value->>'type' IN ('heading', 'paragraph', 'list', 'quote')
      AND block.value->>'text' IS NOT NULL
    GROUP BY r.id
)
UPDATE raw_items r
SET plain_text = rebuilt.plain_text,
    content_hash = encode(sha256(convert_to(rebuilt.plain_text, 'UTF8')), 'hex')
FROM rebuilt
WHERE r.id = rebuilt.id;

INSERT INTO schema_migrations(version) VALUES ('005_fix_plain_text_from_blocks');

COMMIT;
