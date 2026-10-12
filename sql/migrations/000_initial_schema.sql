-- Source copied from D:\Studies\PPR501.22\Group-Assignment\FreshPlan\script.sql.txt on 2026-10-11.
-- This backend-local baseline is safe to re-run and keeps the source schema read-only.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id UUID PRIMARY KEY,
    default_portions SMALLINT NOT NULL DEFAULT 2
        CHECK (default_portions BETWEEN 1 AND 12),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingredients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    shopping_group TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS allergens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingredient_allergens (
    ingredient_id UUID NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
    allergen_id UUID NOT NULL REFERENCES allergens(id) ON DELETE CASCADE,
    PRIMARY KEY (ingredient_id, allergen_id)
);

CREATE TABLE IF NOT EXISTS user_allergies (
    user_id UUID NOT NULL,
    allergen_id UUID NOT NULL REFERENCES allergens(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, allergen_id)
);

CREATE TABLE IF NOT EXISTS inventory_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    ingredient_id UUID REFERENCES ingredients(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    quantity NUMERIC(12, 3) CHECK (quantity IS NULL OR quantity >= 0),
    unit_code TEXT NOT NULL,
    expiry_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, normalized_name, unit_code)
);

CREATE INDEX IF NOT EXISTS ix_inventory_items_user_normalized_name
    ON inventory_items (user_id, normalized_name);
CREATE INDEX IF NOT EXISTS ix_inventory_items_user_expiry_date
    ON inventory_items (user_id, expiry_date);

CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    minutes SMALLINT NOT NULL CHECK (minutes > 0),
    base_servings SMALLINT NOT NULL CHECK (base_servings > 0),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    ingredient_id UUID NOT NULL REFERENCES ingredients(id),
    quantity NUMERIC(12, 3) NOT NULL CHECK (quantity > 0),
    unit_code TEXT NOT NULL,
    position SMALLINT NOT NULL CHECK (position >= 0),
    UNIQUE (recipe_id, position)
);

CREATE TABLE IF NOT EXISTS recipe_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position SMALLINT NOT NULL CHECK (position >= 0),
    text TEXT NOT NULL,
    UNIQUE (recipe_id, position)
);

CREATE TABLE IF NOT EXISTS scan_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'processing', 'succeeded', 'failed', 'confirmed', 'expired')
    ),
    error_code TEXT,
    image_object_key TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS scan_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_job_id UUID NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    suggested_name TEXT NOT NULL,
    ingredient_id UUID REFERENCES ingredients(id) ON DELETE SET NULL,
    confidence NUMERIC(5, 4) CHECK (
        confidence IS NULL OR confidence BETWEEN 0 AND 1
    ),
    position SMALLINT NOT NULL CHECK (position >= 0),
    UNIQUE (scan_job_id, position)
);

CREATE TABLE IF NOT EXISTS weekly_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    week_start DATE NOT NULL,
    draft_portions SMALLINT NOT NULL CHECK (draft_portions BETWEEN 1 AND 12),
    published_revision_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, week_start)
);

CREATE TABLE IF NOT EXISTS weekly_plan_slots (
    weekly_plan_id UUID NOT NULL REFERENCES weekly_plans(id) ON DELETE CASCADE,
    day_index SMALLINT NOT NULL CHECK (day_index BETWEEN 0 AND 6),
    meal_index SMALLINT NOT NULL CHECK (meal_index BETWEEN 0 AND 2),
    recipe_id TEXT NOT NULL REFERENCES recipes(id),
    PRIMARY KEY (weekly_plan_id, day_index, meal_index)
);

CREATE TABLE IF NOT EXISTS saved_plan_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    weekly_plan_id UUID NOT NULL REFERENCES weekly_plans(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    portions SMALLINT NOT NULL CHECK (portions BETWEEN 1 AND 12),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (weekly_plan_id, version),
    UNIQUE (weekly_plan_id, id)
);

CREATE TABLE IF NOT EXISTS saved_plan_slots (
    revision_id UUID NOT NULL REFERENCES saved_plan_revisions(id) ON DELETE CASCADE,
    day_index SMALLINT NOT NULL CHECK (day_index BETWEEN 0 AND 6),
    meal_index SMALLINT NOT NULL CHECK (meal_index BETWEEN 0 AND 2),
    recipe_id TEXT NOT NULL REFERENCES recipes(id),
    PRIMARY KEY (revision_id, day_index, meal_index)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_weekly_plans_published_revision'
    ) THEN
        ALTER TABLE weekly_plans
            ADD CONSTRAINT fk_weekly_plans_published_revision
            FOREIGN KEY (id, published_revision_id)
            REFERENCES saved_plan_revisions (weekly_plan_id, id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS shopping_item_checks (
    revision_id UUID NOT NULL REFERENCES saved_plan_revisions(id) ON DELETE CASCADE,
    ingredient_id UUID NOT NULL REFERENCES ingredients(id),
    dimension_key TEXT NOT NULL,
    is_checked BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (revision_id, ingredient_id, dimension_key)
);

-- Rollback (destructive; run only when the application schema is no longer needed):
-- DROP TABLE IF EXISTS shopping_item_checks, saved_plan_slots, saved_plan_revisions,
--     weekly_plan_slots, weekly_plans, scan_candidates, scan_jobs, recipe_steps,
--     recipe_ingredients, recipes, inventory_items, user_allergies,
--     ingredient_allergens, allergens, ingredients, user_profiles CASCADE;
