BEGIN;

TRUNCATE TABLE
    generated_reports,
    event_revisions,
    event_items,
    news_events,
    normalized_items,
    knowledge_rules,
    glossary_terms,
    review_tasks,
    processing_runs,
    media_extractions,
    ocr_profiles,
    ocr_test_runs
RESTART IDENTITY;

UPDATE media_assets SET ocr_text = NULL WHERE ocr_text IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES ('020_reset_processing_data')
ON CONFLICT (version) DO NOTHING;

COMMIT;
