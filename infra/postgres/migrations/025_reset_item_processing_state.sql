BEGIN;

UPDATE ocr_profiles
SET source_test_run_id = NULL
WHERE source_test_run_id IS NOT NULL;

DELETE FROM ocr_test_runs;

TRUNCATE TABLE
    normalized_item_media_extractions,
    normalized_items,
    knowledge_rules,
    glossary_terms,
    review_tasks,
    processing_runs,
    media_extractions
RESTART IDENTITY;

UPDATE media_assets
SET ocr_text = NULL
WHERE ocr_text IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES ('025_reset_item_processing_state')
ON CONFLICT (version) DO NOTHING;

COMMIT;
