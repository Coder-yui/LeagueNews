BEGIN;

ALTER TABLE event_aggregation_runs
    DROP CONSTRAINT ck_event_runs_outcome,
    ADD CONSTRAINT ck_event_runs_outcome CHECK (
        outcome IS NULL OR outcome IN (
            'skipped_by_minimal_filter', 'applied', 'ignored',
            'review_rejected', 'model_error', 'apply_error'
        )
    );

INSERT INTO schema_migrations(version)
VALUES ('075_allow_event_graph_review_outcome')
ON CONFLICT (version) DO NOTHING;

COMMIT;
