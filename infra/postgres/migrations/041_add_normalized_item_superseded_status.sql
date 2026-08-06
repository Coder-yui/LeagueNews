BEGIN;

ALTER TABLE normalized_items
    DROP CONSTRAINT ck_normalized_items_publication_status,
    ADD CONSTRAINT ck_normalized_items_publication_status
        CHECK (publication_status IN ('published', 'withdrawn', 'superseded'));

INSERT INTO schema_migrations(version)
VALUES ('041_add_normalized_item_superseded_status')
ON CONFLICT (version) DO NOTHING;

COMMIT;
