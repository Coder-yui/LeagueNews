BEGIN;

INSERT INTO sources(name, connector_type, base_url, is_active)
VALUES
    ('Riot Games Official', 'riot_official', 'https://www.leagueoflegends.com/en-us/news/', true),
    ('腾讯英雄联盟官网', 'tencent_lol', 'https://lol.qq.com/', true),
    ('X / Twitter', 'x_twitter', 'https://x.com/', true)
ON CONFLICT (name) DO UPDATE
SET connector_type = EXCLUDED.connector_type,
    base_url = EXCLUDED.base_url;

INSERT INTO schema_migrations(version)
SELECT '008_seed_web_connector_sources'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '008_seed_web_connector_sources'
);

COMMIT;
