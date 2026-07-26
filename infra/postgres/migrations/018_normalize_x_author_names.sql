BEGIN;

UPDATE raw_items r
SET author_name = regexp_replace(r.author_name, '\s+\(@[^()]+\)$', '')
FROM sources s
WHERE s.id = r.source_id
  AND s.connector_type = 'x_twitter'
  AND r.author_name ~ '\s+\(@[^()]+\)$';

INSERT INTO schema_migrations(version)
VALUES ('018_normalize_x_author_names')
ON CONFLICT (version) DO NOTHING;

COMMIT;
