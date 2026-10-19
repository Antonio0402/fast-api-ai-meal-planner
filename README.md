# FreshPlan Backend — FastAPI REST API & AI Service for Meal Planning

FreshPlan Backend is a high-performance REST API and AI service powering the **FreshPlan** mobile application. Built with **FastAPI** and **Python 3.12**, the backend integrates **Google Gemini AI** for multimodal ingredient recognition and personalized recipe generation, provides robust Vietnamese allergy filtering, manages weekly 21-meal scheduling, and computes automated grocery lists.

---

## Live Cloud Deployment (Render)

The backend is deployed and running live on the **Render Cloud Platform**:

- **Production API Base URL**: [https://fast-api-ai-meal-planner.onrender.com](https://fast-api-ai-meal-planner.onrender.com)
- **Interactive Swagger UI**: [https://fast-api-ai-meal-planner.onrender.com/docs](https://fast-api-ai-meal-planner.onrender.com/docs)
- **Alternative ReDoc Explorer**: [https://fast-api-ai-meal-planner.onrender.com/redoc](https://fast-api-ai-meal-planner.onrender.com/redoc)
- **Service Health Check**: [https://fast-api-ai-meal-planner.onrender.com/health](https://fast-api-ai-meal-planner.onrender.com/health) (Returns `{"status": "ok"}`)

---

## Core Capabilities

- **AI-Powered Image Recognition**: Accepts pantry and refrigerator photos (JPEG, PNG, WebP, HEIC/HEIF converted), analyzes images via Google Gemini Vision, and extracts detected ingredient candidates with standard units.
- **Allergy Guard & Keyword Normalization**: Implements Unicode NFC normalization and diacritic-aware matching for Vietnamese ingredients. Excludes cross-reactive allergens (e.g., seafood, tree nuts, peanuts, dairy, eggs, gluten) while preventing homophone false positives (e.g., distinguishing "cá" from "cà", "cua" from "của").
- **Personalized Recipe Generation**: Queries Gemini AI using strict structured JSON schemas to suggest practical recipes utilizing available ingredients, tailored to target portion counts and preparation time limits.
- **21-Meal Weekly Planner**: Schedules meals (Breakfast, Lunch, Dinner) across all 7 days of the week based on selected or AI-generated recipes.
- **Smart Grocery List**: Compares the ingredients required for the weekly meal plan against current inventory, computing exact shortage quantities while converting compatible units (kg ↔ g, l ↔ ml).
- **User State Synchronization**: Handles user inventory and meal schedule snapshots atomically with revision tracking (`If-Match`) and idempotency guarantees (`Idempotency-Key`).
- **Security & JWT Verification**: Validates Supabase Auth tokens (RS256 via JWKS with HS256 fallback), securing all user endpoints and enforcing strict user-level data isolation.

---

## System Architecture & Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| **API Framework** | FastAPI 0.115+ / Starlette | High-performance async web framework |
| **Runtime & Server** | Python 3.12+, Uvicorn (ASGI) | Execution environment and web server |
| **AI Engine** | Google Gemini 2.5 (`gemini-2.5-flash`, `gemini-2.5-pro`) | Multimodal vision & recipe generation via `google-genai` SDK |
| **Database & ORM** | PostgreSQL 16 (Supabase), SQLAlchemy 2.0, Psycopg 3 | Relational database persistence & pooled connections |
| **Authentication** | Supabase Auth (JWT) via `PyJWT` & `httpx` | Token verification & identity extraction |
| **Data Validation** | Pydantic v2 & `pydantic-settings` | Request/response schema validation and environment management |
| **Image Processing** | Pillow (PIL) | Server-side image decoding and magic-byte validation |
| **Cloud Hosting** | Render & Docker | Containerized cloud deployment |

---

## Project Structure

```text
fast-api/
├── app/
│   ├── ai/                 # Gemini client, vendor adapters, prompts, and key routing
│   │   ├── vendor/         # Gemini agent and structured tool execution logic
│   │   ├── gemini_provider.py
│   │   └── prompts.py
│   ├── api/
│   │   └── routes/         # Versioned REST API endpoints
│   │       ├── health.py   # Liveness & readiness probes (/health)
│   │       ├── inventory.py
│   │       ├── meal_plans.py # Weekly plan generation & shopping lists
│   │       ├── recipes.py  # AI recipe generation & pantry matching
│   │       ├── scan_jobs.py# Photo upload & ingredient detection
│   │       └── users.py    # User app data sync (/api/v1/users/me/data)
│   ├── core/               # Configuration, auth verification, and normalization
│   │   ├── auth.py         # Supabase JWT signature validation (JWKS)
│   │   ├── config.py       # Pydantic Settings management (.env)
│   │   └── normalization.py# Unicode and diacritic normalization
│   ├── db/                 # Database connection pooling & migration runner
│   │   ├── migration_runner.py
│   │   └── session.py
│   ├── models/             # SQLAlchemy database models
│   ├── schemas/            # Pydantic request/response schemas
│   │   ├── app_data.py
│   │   ├── recipe.py
│   │   └── weekly_plan.py
│   ├── services/           # Business domain services (Allergy, RecipeGen, Scan)
│   └── main.py             # FastAPI app initialization, middleware, and routers
├── sql/
│   └── migrations/         # Idempotent PostgreSQL migration scripts (000, 001, 002)
├── scripts/
│   ├── run_dev.py          # Development server launcher (0.0.0.0:8000)
│   ├── run_migrations.py   # Database migration execution script
│   └── seed_data.py        # Mock database seeder
├── seed/
│   └── seed_data.sql       # Baseline recipes and ingredients SQL
├── Dockerfile              # Docker container definition
├── Procfile                # Process definition for cloud PaaS
├── render.yaml             # Render infrastructure-as-code deployment configuration
├── requirements.txt        # Production Python dependencies
└── .env.example            # Environment variables template
```

---

## API Endpoints Reference

All application endpoints are versioned under `/api/v1/`:

| Method | Path | Description | Authentication |
|---|---|---|---|
| `GET` | `/health` | Health check endpoint | Public |
| `POST` | `/api/v1/scan-jobs/detect` | Upload ingredient photo for AI analysis | Supabase Bearer JWT |
| `POST` | `/api/v1/recipes/generated` | Generate customized recipes using AI | Supabase Bearer JWT |
| `POST` | `/api/v1/recipes/match` | Match existing pantry items with recipe catalog | Supabase Bearer JWT |
| `POST` | `/api/v1/recipes/by-ids` | Retrieve recipe details by IDs | Supabase Bearer JWT |
| `POST` | `/api/v1/weekly-plans/generate-from-inventory` | Generate 21-meal weekly schedule | Supabase Bearer JWT |
| `POST` | `/api/v1/weekly-plans/shopping/build` | Calculate smart grocery shopping list | Supabase Bearer JWT |
| `GET` | `/api/v1/users/me/data` | Fetch user inventory, weekly slots, and settings | Supabase Bearer JWT |
| `PUT` | `/api/v1/users/me/data` | Atomically save user inventory and meal plans | Supabase Bearer JWT |

---

## Local Development Setup

### Prerequisites

- Python 3.12 or higher
- PostgreSQL database instance (local or Supabase cloud instance)
- Google Gemini API key

### 1. Create Virtual Environment & Install Dependencies

```bash
# Navigate to backend directory
cd fast-api

# Create Python virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Configure the following variables in `.env`:

```dotenv
# Set to 'false' to use live PostgreSQL and Gemini AI
IS_MOCKING=false

# Supabase Auth URL
SUPABASE_URL=https://your-project-ref.supabase.co

# PostgreSQL database connection string (Session pooler or Direct connection)
DATABASE_URL=postgresql+psycopg://postgres:your-password@your-db-host:5432/postgres

# Google Gemini API Key
GEMINI_API_KEY=your-gemini-api-key

# Gemini Model Selection (default: gemini-2.5-flash)
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TIMEOUT_SECONDS=75
```

### 3. Run Database Migrations

Apply idempotent SQL migrations to initialize the schema:

```bash
python -m scripts.run_migrations
```

### 4. Start Development Server

Run the development server listening on `0.0.0.0:8000` (allowing mobile devices on the same Wi-Fi network to connect):

```bash
python -m scripts.run_dev
```

Alternatively:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The interactive API documentation will be available locally at `http://127.0.0.1:8000/docs`.

---

## Cloud Deployment (Docker & Render)

### Running with Docker

Build and run the Docker container locally:

```bash
docker build -t freshplan-api .
docker run -p 8000:8000 --env-file .env freshplan-api
```

### Render Deployment

The repository includes `render.yaml` for automatic continuous deployment:

1. Connect the repository to your Render account.
2. Create a **Blueprint** service pointing to `render.yaml`.
3. Fill in secret environment variables (`DATABASE_URL`, `GEMINI_API_KEY`) in the Render Dashboard.
4. Render automatically builds via `requirements.txt` and starts Uvicorn with zero downtime.
