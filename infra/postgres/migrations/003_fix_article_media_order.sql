BEGIN;

UPDATE raw_items
SET plain_text = split_part(plain_text, E'\n\n[附件]\n', 1)
WHERE title IN (
    'Patch 26.14 Full Preview!',
    '首届海斗大赛-全民赛道7月6日正式启航!'
);

UPDATE normalized_items n
SET normalized_text = r.plain_text,
    updated_at = now()
FROM raw_items r
WHERE n.raw_item_id = r.id
  AND r.title IN (
      'Patch 26.14 Full Preview!',
      '首届海斗大赛-全民赛道7月6日正式启航!'
  );

UPDATE raw_items
SET content_hash = encode(sha256(convert_to(plain_text, 'UTF8')), 'hex')
WHERE title IN (
    'Patch 26.14 Full Preview!',
    '首届海斗大赛-全民赛道7月6日正式启航!'
);

UPDATE raw_items
SET content_blocks = json_build_array(
    json_build_object('type', 'paragraph', 'text', plain_text),
    json_build_object(
        'type', 'image',
        'storage_path', '/media/patch-26-14-preview.jpg',
        'alt_text', 'League of Legends Patch Preview 26.14',
        'caption', '26.14 版本改动预览图'
    )
)
WHERE title = 'Patch 26.14 Full Preview!';

UPDATE raw_items
SET content_blocks = json_build_array(
    json_build_object(
        'type', 'paragraph',
        'text', split_part(plain_text, '在这个夏天书写属于自己的高光时刻!', 1)
                || '在这个夏天书写属于自己的高光时刻!'
    ),
    json_build_object(
        'type', 'image',
        'storage_path', '/media/haidou-tournament-cover.png',
        'alt_text', '首届海斗大赛宣传封面',
        'caption', '首届海斗大赛宣传封面'
    ),
    json_build_object(
        'type', 'paragraph',
        'text', split_part(
                    split_part(plain_text, '在这个夏天书写属于自己的高光时刻!', 2),
                    '组成6支全民赛道战队，前往线下总决赛。',
                    1
                ) || '组成6支全民赛道战队，前往线下总决赛。'
    ),
    json_build_object(
        'type', 'image',
        'storage_path', '/media/haidou-tournament-details.png',
        'alt_text', '海斗大赛全民赛道赛程与晋级信息',
        'caption', '全民赛道赛程与晋级信息'
    ),
    json_build_object(
        'type', 'paragraph',
        'text', split_part(
                    split_part(plain_text, '在这个夏天书写属于自己的高光时刻!', 2),
                    '组成6支全民赛道战队，前往线下总决赛。',
                    2
                )
    )
)
WHERE title = '首届海斗大赛-全民赛道7月6日正式启航!';

UPDATE media_assets
SET block_index = 3
WHERE storage_path = '/media/haidou-tournament-details.png';

INSERT INTO schema_migrations(version) VALUES ('003_fix_article_media_order');

COMMIT;

