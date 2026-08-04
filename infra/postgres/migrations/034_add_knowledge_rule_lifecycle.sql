BEGIN;

ALTER TABLE knowledge_rules
    ADD COLUMN lifecycle_status varchar(20),
    ADD COLUMN evaluation_summary json NOT NULL DEFAULT '{}'::json,
    ADD COLUMN evaluated_at timestamptz,
    ADD COLUMN promoted_at timestamptz,
    ADD COLUMN retired_at timestamptz;

UPDATE knowledge_rules
SET lifecycle_status = CASE WHEN is_active THEN 'active' ELSE 'retired' END
WHERE lifecycle_status IS NULL;

ALTER TABLE knowledge_rules
    ALTER COLUMN lifecycle_status SET NOT NULL,
    ALTER COLUMN lifecycle_status SET DEFAULT 'draft',
    ALTER COLUMN is_active SET DEFAULT false,
    ADD CONSTRAINT ck_knowledge_rules_lifecycle
        CHECK (lifecycle_status IN ('draft', 'evaluated', 'active', 'retired')),
    ADD CONSTRAINT ck_knowledge_rules_active_lifecycle
        CHECK ((lifecycle_status = 'active') = is_active);

CREATE INDEX ix_knowledge_rules_lifecycle_status
    ON knowledge_rules(lifecycle_status);

INSERT INTO schema_migrations(version)
VALUES ('034_add_knowledge_rule_lifecycle')
ON CONFLICT (version) DO NOTHING;

COMMIT;
