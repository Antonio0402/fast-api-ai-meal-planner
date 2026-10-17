from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from app.core.errors import AUTH_ERROR_RESPONSES, install_error_handlers
from app.core.config import settings

from app.api.routes import health, meal_plans, recipes, users, scan_jobs
from app.services.scan_service import reconcile_scan_jobs


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await run_in_threadpool(reconcile_scan_jobs)
    yield


app = FastAPI(
    title="FreshMeal AI Planner API",
    version="1.0.0",
    lifespan=lifespan,
)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.web_origins.split(",") if origin.strip()],
    allow_origin_regex=settings.cors_origin_regex,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key"],
)

app.include_router(health.router)
app.include_router(users.router, prefix="/api/v1/users", tags=["users"], responses=AUTH_ERROR_RESPONSES)
# Tạm tắt route inventory-items: FreshPlan tải/lưu kho qua users/me/data.
app.include_router(recipes.router, prefix="/api/v1/recipes", tags=["recipes"], responses=AUTH_ERROR_RESPONSES)
app.include_router(meal_plans.router, prefix="/api/v1/weekly-plans", tags=["weekly-plans"], responses=AUTH_ERROR_RESPONSES)
app.include_router(scan_jobs.router, prefix="/api/v1/scan-jobs", tags=["scan-jobs"], responses=AUTH_ERROR_RESPONSES)
