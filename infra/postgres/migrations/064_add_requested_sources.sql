BEGIN;

INSERT INTO sources (
    name,
    connector_type,
    external_key,
    base_url,
    connector_config,
    is_active,
    is_official,
    reliability_score
)
VALUES
    (
        'League of Legends Dev Team (@LoLDev)',
        'x_twitter',
        'loldev',
        'https://x.com/LoLDev',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'Riot Phlox (@RiotPhlox)',
        'x_twitter',
        'riotphlox',
        'https://x.com/RiotPhlox',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'LCK (@LCK)',
        'x_twitter',
        'lck',
        'https://x.com/LCK',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'LEC (@LEC)',
        'x_twitter',
        'lec',
        'https://x.com/LEC',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'T1 LoL (@T1LoL)',
        'x_twitter',
        't1lol',
        'https://x.com/T1LoL',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'Gen.G Esports (@GenG)',
        'x_twitter',
        'geng',
        'https://x.com/GenG',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'G2 League of Legends (@G2League)',
        'x_twitter',
        'g2league',
        'https://x.com/G2League',
        '{}'::json,
        true,
        true,
        1.0
    ),
    (
        'BLG电子竞技俱乐部',
        'weibo',
        '5926660141',
        'https://weibo.com/u/5926660141',
        '{"include_reposts": true}'::json,
        true,
        true,
        1.0
    ),
    (
        '滔搏电子竞技俱乐部',
        'weibo',
        '5449734852',
        'https://weibo.com/u/5449734852',
        '{"include_reposts": true}'::json,
        true,
        true,
        1.0
    ),
    (
        '丶涵艺',
        'weibo',
        '1992350413',
        'https://weibo.com/u/1992350413',
        '{"include_reposts": true}'::json,
        true,
        false,
        0.6
    )
ON CONFLICT DO NOTHING;

UPDATE sources AS source
SET
    name = requested.name,
    base_url = requested.base_url,
    connector_config = requested.connector_config,
    is_active = true,
    is_official = requested.is_official,
    reliability_score = requested.reliability_score
FROM (
    VALUES
        ('League of Legends Dev Team (@LoLDev)', 'x_twitter', 'loldev', 'https://x.com/LoLDev', '{}'::json, true, 1.0::double precision),
        ('Riot Phlox (@RiotPhlox)', 'x_twitter', 'riotphlox', 'https://x.com/RiotPhlox', '{}'::json, true, 1.0::double precision),
        ('LCK (@LCK)', 'x_twitter', 'lck', 'https://x.com/LCK', '{}'::json, true, 1.0::double precision),
        ('LEC (@LEC)', 'x_twitter', 'lec', 'https://x.com/LEC', '{}'::json, true, 1.0::double precision),
        ('T1 LoL (@T1LoL)', 'x_twitter', 't1lol', 'https://x.com/T1LoL', '{}'::json, true, 1.0::double precision),
        ('Gen.G Esports (@GenG)', 'x_twitter', 'geng', 'https://x.com/GenG', '{}'::json, true, 1.0::double precision),
        ('G2 League of Legends (@G2League)', 'x_twitter', 'g2league', 'https://x.com/G2League', '{}'::json, true, 1.0::double precision),
        ('BLG电子竞技俱乐部', 'weibo', '5926660141', 'https://weibo.com/u/5926660141', '{"include_reposts": true}'::json, true, 1.0::double precision),
        ('滔搏电子竞技俱乐部', 'weibo', '5449734852', 'https://weibo.com/u/5449734852', '{"include_reposts": true}'::json, true, 1.0::double precision),
        ('丶涵艺', 'weibo', '1992350413', 'https://weibo.com/u/1992350413', '{"include_reposts": true}'::json, false, 0.6::double precision)
) AS requested(
    name,
    connector_type,
    external_key,
    base_url,
    connector_config,
    is_official,
    reliability_score
)
WHERE source.connector_type = requested.connector_type
  AND source.external_key = requested.external_key;

INSERT INTO sources (
    name,
    connector_type,
    external_key,
    base_url,
    connector_config,
    is_active,
    is_official,
    reliability_score
)
VALUES (
    '腾讯英雄联盟赛事官网（LPL）',
    'tencent_lol',
    NULL,
    'https://lol.qq.com/',
    '{"target": "25"}'::json,
    true,
    true,
    1.0
)
ON CONFLICT DO NOTHING;

UPDATE sources
SET
    connector_type = 'tencent_lol',
    external_key = NULL,
    base_url = 'https://lol.qq.com/',
    connector_config = '{"target": "25"}'::json,
    is_active = true,
    is_official = true,
    reliability_score = 1.0
WHERE name = '腾讯英雄联盟赛事官网（LPL）';

INSERT INTO schema_migrations(version)
VALUES ('064_add_requested_sources')
ON CONFLICT (version) DO NOTHING;

COMMIT;
