import hashlib
import json
from datetime import date, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.errors import APIError
from app.db.session import get_db
from app.schemas.app_data import AppData, AppDataResponse
from app.schemas.profile import ProfilePatch
from app.services.db_utils import fetch_one

router = APIRouter()


@router.get("/me/data", response_model=AppDataResponse, response_model_exclude_unset=True)
def get_app_data(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = fetch_one(
        db, "SELECT data, revision FROM app_data_snapshots WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    if row:
        return {"data": row["data"], "revision": row["revision"]}
    today = date.today()
    return {"data": {"inventory": [], "portions": 2, "allergies": [],
                     "weekStart": (today - timedelta(days=today.weekday())).isoformat(),
                     "slots": {}, "checked": {}}, "revision": 0}


@router.put("/me/data", response_model=AppDataResponse, response_model_exclude_unset=True)
def put_app_data(
    payload: AppData,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    data = payload.model_dump(mode="json", exclude_none=True)
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": f"app-data:{user_id}"})
        previous = fetch_one(db, """
            SELECT request_hash, response_json FROM app_data_mutations
            WHERE user_id = :user_id AND mutation_id = :mutation_id
            """, {"user_id": user_id, "mutation_id": idempotency_key})
        if previous:
            if previous["request_hash"] != fingerprint:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "Khóa lưu đã dùng cho dữ liệu khác.")
            return previous["response_json"]
        current = fetch_one(db, "SELECT revision FROM app_data_snapshots WHERE user_id = :user_id FOR UPDATE",
                            {"user_id": user_id})
        revision = current["revision"] if current else 0
        if if_match != f'"{revision}"':
            raise APIError(412, "REVISION_CONFLICT", "Dữ liệu đã thay đổi.")
        result = {"data": data, "revision": revision + 1}
        db.execute(text("""
            INSERT INTO app_data_snapshots (user_id, data, revision)
            VALUES (:user_id, CAST(:data AS jsonb), :revision)
            ON CONFLICT (user_id) DO UPDATE
            SET data = EXCLUDED.data, revision = EXCLUDED.revision, updated_at = now()
            """), {"user_id": user_id, "data": serialized, "revision": revision + 1})
        db.execute(text("""
            INSERT INTO app_data_mutations (user_id, mutation_id, request_hash, response_json)
            VALUES (:user_id, :mutation_id, :request_hash, CAST(:response AS jsonb))
            """), {"user_id": user_id, "mutation_id": idempotency_key,
                    "request_hash": fingerprint, "response": json.dumps(result, ensure_ascii=False)})
    return result

""" Frontend has been use the Supabase Auth API to get the user ID. It does not need to get it from the database."""
@router.get("/me/profile")
def get_profile(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with db.begin():
        row = fetch_one(
            db,
            """
            INSERT INTO user_profiles (user_id, default_portions)
            VALUES (:user_id, 2)
            ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
            RETURNING user_id, default_portions
            """,
            {"user_id": user_id},
        )
    return {"user_id": str(row["user_id"]), "default_portions": row["default_portions"]}


@router.patch("/me/profile")
def patch_profile(
    payload: ProfilePatch,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with db.begin():
        row = fetch_one(
            db,
            """
            INSERT INTO user_profiles (user_id, default_portions)
            VALUES (:user_id, :default_portions)
            ON CONFLICT (user_id) DO UPDATE
            SET default_portions = EXCLUDED.default_portions,
                updated_at = now()
            RETURNING user_id, default_portions
            """,
            {
                "user_id": user_id,
                "default_portions": payload.default_portions,
            },
        )
    return {"user_id": str(row["user_id"]), "default_portions": row["default_portions"]}
