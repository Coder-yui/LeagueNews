BEGIN;

ALTER TABLE sources
ADD COLUMN IF NOT EXISTS external_key varchar(255);

ALTER TABLE sources
ADD COLUMN IF NOT EXISTS connector_config json NOT NULL DEFAULT '{}'::json;

DO $$
DECLARE
    target_id integer;
    generic_id integer;
BEGIN
    SELECT id INTO target_id
    FROM sources
    WHERE base_url ~* '^https://x\.com/RiotPhroxzon/?$'
       OR name ILIKE '%RiotPhroxzon%'
    ORDER BY id
    LIMIT 1;

    SELECT id INTO generic_id
    FROM sources
    WHERE connector_type = 'x_twitter'
      AND (target_id IS NULL OR id <> target_id)
    ORDER BY id
    LIMIT 1;

    IF target_id IS NULL THEN
        target_id := generic_id;
        generic_id := NULL;
    END IF;

    IF target_id IS NOT NULL AND generic_id IS NOT NULL THEN
        UPDATE raw_items SET source_id = target_id WHERE source_id = generic_id;
        UPDATE connector_runs SET source_id = target_id WHERE source_id = generic_id;
        DELETE FROM sources WHERE id = generic_id;
    END IF;

    IF target_id IS NOT NULL THEN
        UPDATE sources
        SET name = 'Matt Leung-Harrison (@RiotPhroxzon)',
            connector_type = 'x_twitter',
            external_key = 'riotphroxzon',
            base_url = 'https://x.com/RiotPhroxzon',
            connector_config = '{}'::json
        WHERE id = target_id;
    END IF;
END $$;

DO $$
DECLARE
    target_id integer;
    generic_id integer;
BEGIN
    SELECT id INTO target_id
    FROM sources
    WHERE connector_type = 'manual'
      AND base_url = 'https://lol.qq.com/'
    ORDER BY id
    LIMIT 1;

    SELECT id INTO generic_id
    FROM sources
    WHERE connector_type = 'tencent_lol'
      AND (target_id IS NULL OR id <> target_id)
    ORDER BY id
    LIMIT 1;

    IF target_id IS NULL THEN
        target_id := generic_id;
        generic_id := NULL;
    END IF;

    IF target_id IS NOT NULL AND generic_id IS NOT NULL THEN
        UPDATE raw_items SET source_id = target_id WHERE source_id = generic_id;
        UPDATE connector_runs SET source_id = target_id WHERE source_id = generic_id;
        DELETE FROM sources WHERE id = generic_id;
    END IF;

    IF target_id IS NOT NULL THEN
        UPDATE sources
        SET name = '腾讯英雄联盟官方网站',
            connector_type = 'tencent_lol',
            external_key = 'lol.qq.com',
            base_url = 'https://lol.qq.com/',
            connector_config = '{"target": "24"}'::json
        WHERE id = target_id;
    END IF;
END $$;

UPDATE sources
SET external_key = 'leagueoflegends.com',
    connector_config = '{}'::json
WHERE connector_type = 'riot_official';

CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_connector_external_key
ON sources(connector_type, external_key)
WHERE external_key IS NOT NULL;

INSERT INTO schema_migrations(version)
SELECT '009_source_identity_per_publisher'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '009_source_identity_per_publisher'
);

COMMIT;
