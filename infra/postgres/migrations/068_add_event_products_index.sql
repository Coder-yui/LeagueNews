BEGIN;

CREATE INDEX ix_events_products_gin
    ON events USING gin(products);

INSERT INTO schema_migrations(version)
VALUES ('068_add_event_products_index');

COMMIT;
