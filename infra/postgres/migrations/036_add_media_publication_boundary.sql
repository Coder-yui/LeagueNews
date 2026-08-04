BEGIN;

ALTER TABLE media_assets
    ADD COLUMN public_path varchar(1000),
    ADD COLUMN visibility varchar(30) NOT NULL DEFAULT 'private',
    ADD COLUMN published_at timestamptz,
    ADD CONSTRAINT ck_media_assets_visibility
        CHECK (visibility IN ('private', 'published', 'legacy_public'));

UPDATE media_assets
SET public_path = storage_path,
    visibility = 'legacy_public',
    published_at = now()
WHERE storage_path LIKE '/media/%';

CREATE INDEX ix_media_assets_visibility ON media_assets(visibility);

INSERT INTO schema_migrations(version)
VALUES ('036_add_media_publication_boundary')
ON CONFLICT (version) DO NOTHING;

COMMIT;
