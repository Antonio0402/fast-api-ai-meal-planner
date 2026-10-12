# FreshMeal AI Planner Backend Build Report

## 1. Scope and outcome

Implemented the FastAPI MVP backend in this repository only. The mobile app and
the separate AI project were not changed, and no mobile-to-backend integration
was added. The application exposes 18 OpenAPI operations across 14 paths,
including the health endpoint.

## 2. Application structure

- `app/main.py` assembles the API routers, common error handlers, and startup
  reconciliation for scan jobs.
- `app/core/` contains settings, Supabase JWT verification, error responses,
  ingredient-name normalization, and private scan-image storage.
- `app/api/routes/` implements profile, inventory, recipe, weekly-plan,
  shopping-list, and scan-job endpoints.
- `app/services/` contains inventory, meal-plan, and scan workflows.
- `app/ai/` contains the backend AI client and the vendored scan implementation.

## 3. Authentication and request safety

Protected routes validate Supabase JWT signatures and claims, using JWKS and an
HS256 fallback where configured. The API bootstraps a profile for an
authenticated user when needed. Request validation and database conflicts use
the shared JSON error envelope. Ingredient keys use Unicode NFC, case folding,
Vietnamese diacritic removal, trimming, and whitespace collapsing.

## 4. API coverage

Implemented profile read/update; inventory list/create/update/delete and
clear-all; recipe listing; weekly-plan read/generate/draft/publish; shopping
list and check updates; and scan upload/status/confirmation. Inventory and
recipe list defaults are 100 rows with a 200-row maximum. Shopping lists are
not paginated. Weekly plans require a Monday start date.

## 5. Database changes and seed data

Added backend-local SQL migrations and a migration runner. The baseline creates
the MVP tables and indexes; the follow-up migration adds scan and inventory
idempotency data plus foreign keys. User foreign keys are introduced as
`NOT VALID` so existing orphan rows do not block deployment; they still enforce
new writes. The seed contains nine recipes and their recipe ingredients, steps,
and shopping groups.

The test fixture applied migrations and seed data to the dedicated local
PostgreSQL database `freshplandb`. No remote database was used. The application
does not apply migrations automatically at startup; review migrations and run
them explicitly against the intended database.

## 6. AI scan workflow

Scan uploads require an idempotency key and accept validated JPEG, PNG, and
WebP images up to 5 MB. Validation checks media type, file signature, and image
decoding. Jobs use `BackgroundTasks`, bounded worker concurrency, normalized
candidate output, and stable public error codes. Confirm requests are
idempotent and can add accepted candidates to inventory. Expired jobs are
returned with HTTP 200 and their private image files are removed; stuck
processing jobs are reconciled.

The backend reads `GEMINI_API_KEY` and `GEMINI_MODEL`; it does not use the
vendored implementation's key-file loader. Automated tests replace Gemini with
a fake client.

## 7. Meal-planning and shopping behavior

The weekly-plan workflow supports generated suggestions, editable drafts, and
versioned published revisions. If AI generation fails, a deterministic
rotation of active recipes is used. Shopping quantities are aggregated and
converted using the unit-alias table where conversion is supported; items that
cannot be converted are kept separate. Checked items are persisted per
published revision.

## 8. Validation performed

- `pytest -q`: **20 passed** against local PostgreSQL.
- `compileall -q app tests`: passed.
- OpenAPI generation: **14 paths / 18 operations**.
- Pylance diagnostics for the adjusted weekly-plan route and SQLAlchemy model
  relationship files: no diagnostics.
- The test run reports one Starlette deprecation warning regarding its
  `httpx` TestClient integration; it does not fail the suite.
- `ruff` is not installed in the project virtual environment, and the project
  has no configured Python linter.

## 9. Operational limitations and next steps

Scan work is in-process and image files are stored locally under `var/scans/`.
This is suitable only for a single-process MVP: jobs are not durable across
process restarts, and local files are not shared between API instances. Before
multi-instance deployment, move images to private shared object storage and
processing to a durable queue/worker. After reviewing legacy user rows, validate
the deferred foreign-key constraints. Keep `.env` out of version control, set
production secrets through the deployment environment, and run migrations
separately from application startup.
