BEGIN;

INSERT INTO ocr_profiles (
    name,
    parameters,
    source_test_run_id,
    is_active
)
SELECT
    'production-2026-07-25',
    '{
      "scale": 2,
      "grayscale": false,
      "contrast": 1,
      "sharpness": 1,
      "text_score": null,
      "box_thresh": null,
      "unclip_ratio": 1.2,
      "use_cls": true,
      "divider_x_ratio": null,
      "line_brightness": 105,
      "line_coverage": 0.82
    }'::json,
    NULL,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM ocr_profiles WHERE is_active = true
);

INSERT INTO schema_migrations(version)
VALUES ('024_restore_production_ocr_profile')
ON CONFLICT (version) DO NOTHING;

COMMIT;
