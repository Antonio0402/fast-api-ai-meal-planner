-- Add backend-required auth links, scan fingerprints and inventory request replay cache.
ALTER TABLE scan_jobs ADD COLUMN IF NOT EXISTS request_hash TEXT;
ALTER TABLE scan_jobs ADD COLUMN IF NOT EXISTS confirm_hash TEXT;
ALTER TABLE scan_jobs ADD COLUMN IF NOT EXISTS confirm_response JSONB;

CREATE INDEX IF NOT EXISTS ix_scan_jobs_status_created_at
    ON scan_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS ix_scan_jobs_user_created_at
    ON scan_jobs (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS inventory_idempotency (
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, idempotency_key)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_user_profiles_auth_user'
          AND conrelid = 'user_profiles'::regclass
    ) THEN
        ALTER TABLE user_profiles
            ADD CONSTRAINT fk_user_profiles_auth_user
            FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_inventory_items_user_profile'
          AND conrelid = 'inventory_items'::regclass
    ) THEN
        ALTER TABLE inventory_items
            ADD CONSTRAINT fk_inventory_items_user_profile
            FOREIGN KEY (user_id) REFERENCES user_profiles(user_id) ON DELETE CASCADE
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_user_allergies_user_profile'
          AND conrelid = 'user_allergies'::regclass
    ) THEN
        ALTER TABLE user_allergies
            ADD CONSTRAINT fk_user_allergies_user_profile
            FOREIGN KEY (user_id) REFERENCES user_profiles(user_id) ON DELETE CASCADE
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_scan_jobs_user_profile'
          AND conrelid = 'scan_jobs'::regclass
    ) THEN
        ALTER TABLE scan_jobs
            ADD CONSTRAINT fk_scan_jobs_user_profile
            FOREIGN KEY (user_id) REFERENCES user_profiles(user_id) ON DELETE CASCADE
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_weekly_plans_user_profile'
          AND conrelid = 'weekly_plans'::regclass
    ) THEN
        ALTER TABLE weekly_plans
            ADD CONSTRAINT fk_weekly_plans_user_profile
            FOREIGN KEY (user_id) REFERENCES user_profiles(user_id) ON DELETE CASCADE
            NOT VALID;
    END IF;
END $$;

-- Rollback (retain the baseline tables):
-- ALTER TABLE weekly_plans DROP CONSTRAINT IF EXISTS fk_weekly_plans_user_profile;
-- ALTER TABLE scan_jobs DROP CONSTRAINT IF EXISTS fk_scan_jobs_user_profile;
-- ALTER TABLE user_allergies DROP CONSTRAINT IF EXISTS fk_user_allergies_user_profile;
-- ALTER TABLE inventory_items DROP CONSTRAINT IF EXISTS fk_inventory_items_user_profile;
-- ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS fk_user_profiles_auth_user;
-- DROP TABLE IF EXISTS inventory_idempotency;
-- DROP INDEX IF EXISTS ix_scan_jobs_user_created_at;
-- DROP INDEX IF EXISTS ix_scan_jobs_status_created_at;
-- ALTER TABLE scan_jobs DROP COLUMN IF EXISTS confirm_response;
-- ALTER TABLE scan_jobs DROP COLUMN IF EXISTS confirm_hash;
-- ALTER TABLE scan_jobs DROP COLUMN IF EXISTS request_hash;
