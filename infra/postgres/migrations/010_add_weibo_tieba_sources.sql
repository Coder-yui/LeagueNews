BEGIN;

INSERT INTO sources (name, connector_type, external_key, base_url, connector_config, is_active)
VALUES
    ('英雄联盟赛事', 'weibo', '5756404150', 'https://weibo.com/u/5756404150', '{"include_reposts": true}'::json, true),
    ('英雄联盟', 'weibo', '5720474518', 'https://weibo.com/u/5720474518', '{"include_reposts": true}'::json, true),
    ('恋恋红茶_244', 'weibo', '2266865584', 'https://weibo.com/u/2266865584', '{"include_reposts": true}'::json, true),
    ('召唤师Park', 'weibo', '2522098777', 'https://weibo.com/u/2522098777', '{"include_reposts": true}'::json, true),
    ('_尧阿尧y_', 'weibo', '2600241232', 'https://weibo.com/u/2600241232', '{"include_reposts": true}'::json, true),
    (
        'lol半价吧 · 小老鼠小伟',
        'baidu_tieba',
        '86124184',
        'https://tieba.baidu.com/home/main?id=tb.1.1d0b2530.0ZbI4ZqXy-dJplytHVhuQQ',
        '{"forum_name": "lol半价", "max_thread_pages": 5, "max_post_pages": 100}'::json,
        true
    ),
    (
        'lol半价吧 · 凤舞天_惊鸿恋',
        'baidu_tieba',
        '770437943',
        'https://tieba.baidu.com/home/main?id=tb.1.dda57dd7.f1PcHOitsXB66qcRaCI4kQ',
        '{"forum_name": "lol半价", "max_thread_pages": 5, "max_post_pages": 100}'::json,
        true
    )
ON CONFLICT DO NOTHING;

UPDATE sources
SET name = '英雄联盟赛事',
    base_url = 'https://weibo.com/u/5756404150',
    connector_config = '{"include_reposts": true}'::json,
    is_active = true
WHERE connector_type = 'weibo' AND external_key = '5756404150';

UPDATE sources
SET name = '英雄联盟',
    base_url = 'https://weibo.com/u/5720474518',
    connector_config = '{"include_reposts": true}'::json,
    is_active = true
WHERE connector_type = 'weibo' AND external_key = '5720474518';

UPDATE sources
SET name = '恋恋红茶_244',
    base_url = 'https://weibo.com/u/2266865584',
    connector_config = '{"include_reposts": true}'::json,
    is_active = true
WHERE connector_type = 'weibo' AND external_key = '2266865584';

UPDATE sources
SET name = '召唤师Park',
    base_url = 'https://weibo.com/u/2522098777',
    connector_config = '{"include_reposts": true}'::json,
    is_active = true
WHERE connector_type = 'weibo' AND external_key = '2522098777';

UPDATE sources
SET name = '_尧阿尧y_',
    base_url = 'https://weibo.com/u/2600241232',
    connector_config = '{"include_reposts": true}'::json,
    is_active = true
WHERE connector_type = 'weibo' AND external_key = '2600241232';

UPDATE sources
SET name = 'lol半价吧 · 小老鼠小伟',
    base_url = 'https://tieba.baidu.com/home/main?id=tb.1.1d0b2530.0ZbI4ZqXy-dJplytHVhuQQ',
    connector_config = '{"forum_name": "lol半价", "max_thread_pages": 5, "max_post_pages": 100}'::json,
    is_active = true
WHERE connector_type = 'baidu_tieba' AND external_key = '86124184';

UPDATE sources
SET name = 'lol半价吧 · 凤舞天_惊鸿恋',
    base_url = 'https://tieba.baidu.com/home/main?id=tb.1.dda57dd7.f1PcHOitsXB66qcRaCI4kQ',
    connector_config = '{"forum_name": "lol半价", "max_thread_pages": 5, "max_post_pages": 100}'::json,
    is_active = true
WHERE connector_type = 'baidu_tieba' AND external_key = '770437943';

INSERT INTO schema_migrations(version)
SELECT '010_add_weibo_tieba_sources'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '010_add_weibo_tieba_sources'
);

COMMIT;
