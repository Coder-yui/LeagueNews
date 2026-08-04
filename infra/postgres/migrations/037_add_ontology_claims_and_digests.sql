BEGIN;

ALTER TABLE normalized_items
    ADD COLUMN primary_topic varchar(40) NOT NULL DEFAULT 'other',
    ADD COLUMN secondary_topics jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN facets jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN ontology_version varchar(40) NOT NULL DEFAULT 'lol-news-v1',
    ADD COLUMN importance_dimensions jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN importance_policy_version varchar(80)
        NOT NULL DEFAULT 'importance-v1-five-dimensions',
    ADD COLUMN importance_calculation jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN credibility_components jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN credibility_policy_version varchar(80)
        NOT NULL DEFAULT 'credibility-v1-components';

UPDATE normalized_items
SET primary_topic = CASE
    WHEN category ILIKE ANY (ARRAY['%版本%', '%平衡%', '%patch%']) THEN 'patch'
    WHEN category ILIKE ANY (ARRAY['%赛事%', '%赛程%', '%赛果%', '%比赛%']) THEN 'esports'
    WHEN category ILIKE ANY (ARRAY['%转会%', '%阵容%', '%退役%']) THEN 'roster'
    WHEN category ILIKE '%英雄%' THEN 'champion'
    WHEN category ILIKE '%皮肤%' THEN 'skin'
    WHEN category ILIKE ANY (ARRAY['%活动%', '%商城%']) THEN 'activity'
    WHEN category ILIKE ANY (ARRAY['%模式%', '%玩法%']) THEN 'game_mode'
    ELSE 'other'
END,
credibility_components = jsonb_build_object(
    'source_prior', credibility_score,
    'source_role', CASE WHEN credibility = 'official' THEN 'first_party' ELSE 'unknown' END,
    'legacy_backfill', true
);

CREATE INDEX ix_normalized_items_primary_topic
    ON normalized_items(primary_topic);

CREATE TABLE claims (
    id bigserial PRIMARY KEY,
    normalized_item_id bigint NOT NULL REFERENCES normalized_items(id) ON DELETE CASCADE,
    subject jsonb NOT NULL DEFAULT '{}'::jsonb,
    predicate varchar(120) NOT NULL,
    object_value jsonb NOT NULL DEFAULT '{}'::jsonb,
    before_value jsonb,
    after_value jsonb,
    effective_at timestamptz,
    stance varchar(20) NOT NULL DEFAULT 'asserts',
    claim_type varchar(40) NOT NULL DEFAULT 'statement',
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    extraction_model varchar(120) NOT NULL,
    schema_version varchar(40) NOT NULL DEFAULT 'claim-v1',
    confidence double precision NOT NULL DEFAULT 1,
    status varchar(20) NOT NULL DEFAULT 'active',
    revision integer NOT NULL DEFAULT 1,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_claims_stance
        CHECK (stance IN ('asserts', 'supports', 'contradicts', 'context')),
    CONSTRAINT ck_claims_status
        CHECK (status IN ('active', 'superseded', 'withdrawn')),
    CONSTRAINT ck_claims_revision CHECK (revision >= 1)
);
CREATE INDEX ix_claims_normalized_item_id ON claims(normalized_item_id);
CREATE INDEX ix_claims_effective_at ON claims(effective_at);
CREATE INDEX ix_claims_stance ON claims(stance);
CREATE INDEX ix_claims_claim_type ON claims(claim_type);
CREATE INDEX ix_claims_status ON claims(status);

CREATE TABLE event_claims (
    event_id bigint NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    claim_id bigint NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    relation varchar(20) NOT NULL DEFAULT 'supports',
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, claim_id)
);

CREATE TABLE digests (
    id bigserial PRIMARY KEY,
    digest_type varchar(20) NOT NULL,
    timezone varchar(80) NOT NULL,
    window_start timestamptz NOT NULL,
    cutoff_at timestamptz NOT NULL,
    language varchar(20) NOT NULL DEFAULT 'zh-CN',
    title varchar(500) NOT NULL,
    body text NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'published',
    current_revision integer NOT NULL DEFAULT 1,
    input_hash varchar(64) NOT NULL,
    input_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
    generation_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    published_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_digests_window UNIQUE (digest_type, timezone, cutoff_at),
    CONSTRAINT ck_digests_type CHECK (digest_type IN ('daily', 'weekly')),
    CONSTRAINT ck_digests_status CHECK (status IN ('published', 'withdrawn'))
);
CREATE INDEX ix_digests_digest_type ON digests(digest_type);
CREATE INDEX ix_digests_window_start ON digests(window_start);
CREATE INDEX ix_digests_cutoff_at ON digests(cutoff_at);
CREATE INDEX ix_digests_status ON digests(status);
CREATE INDEX ix_digests_published_at ON digests(published_at);

CREATE TABLE digest_revisions (
    id bigserial PRIMARY KEY,
    digest_id bigint NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    title varchar(500) NOT NULL,
    body text NOT NULL,
    input_hash varchar(64) NOT NULL,
    input_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
    change_note text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_digest_revisions UNIQUE (digest_id, revision),
    CONSTRAINT ck_digest_revisions_revision CHECK (revision >= 1)
);
CREATE INDEX ix_digest_revisions_digest_id ON digest_revisions(digest_id);
CREATE INDEX ix_digest_revisions_created_at ON digest_revisions(created_at);

INSERT INTO schema_migrations(version)
VALUES ('037_add_ontology_claims_and_digests')
ON CONFLICT (version) DO NOTHING;

COMMIT;
