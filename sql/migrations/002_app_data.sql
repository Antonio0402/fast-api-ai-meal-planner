-- Snapshot used by the mobile application's revision-aware /me/data contract.
CREATE TABLE IF NOT EXISTS app_data_snapshots (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    data JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_data_mutations (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    mutation_id UUID NOT NULL,
    request_hash TEXT NOT NULL,
    response_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, mutation_id)
);

-- Only the FastAPI database role should access snapshots; mobile clients use the API.
ALTER TABLE app_data_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_data_mutations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON app_data_snapshots, app_data_mutations FROM anon, authenticated;

-- AI output belongs to the authenticated user; there is no preloaded recipe catalog.
CREATE TABLE IF NOT EXISTS user_recipes (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    id UUID NOT NULL,
    recipe JSONB NOT NULL,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS ix_user_recipes_source
    ON user_recipes (user_id, source_hash);
ALTER TABLE user_recipes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON user_recipes FROM anon, authenticated;
