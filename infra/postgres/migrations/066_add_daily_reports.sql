BEGIN;

CREATE TABLE daily_reports (
    id serial PRIMARY KEY,
    report_date date NOT NULL UNIQUE,
    status varchar(20) NOT NULL DEFAULT 'published',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_daily_reports_status CHECK (status IN ('published', 'withdrawn'))
);

CREATE INDEX ix_daily_reports_status ON daily_reports(status);

CREATE TABLE daily_report_items (
    id serial PRIMARY KEY,
    report_id integer NOT NULL REFERENCES daily_reports(id) ON DELETE CASCADE,
    normalized_item_id integer NOT NULL REFERENCES normalized_items(id) ON DELETE RESTRICT,
    section varchar(20) NOT NULL,
    position integer NOT NULL,
    CONSTRAINT uq_daily_report_item_message UNIQUE (report_id, normalized_item_id),
    CONSTRAINT uq_daily_report_item_position UNIQUE (report_id, section, position),
    CONSTRAINT ck_daily_report_items_section CHECK (section IN ('lolpc', 'esports', 'tft', 'other')),
    CONSTRAINT ck_daily_report_items_position CHECK (position >= 1)
);

CREATE INDEX ix_daily_report_items_report_id ON daily_report_items(report_id);
CREATE INDEX ix_daily_report_items_message_id ON daily_report_items(normalized_item_id);

INSERT INTO schema_migrations(version)
VALUES ('066_add_daily_reports')
ON CONFLICT (version) DO NOTHING;

COMMIT;
