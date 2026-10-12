from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.errors import APIError
from app.core.normalization import normalize_name
from app.db.session import get_db
from app.schemas.inventory import InventoryBatchInput, InventoryItemPatch
from app.services.db_utils import fetch_all, fetch_one
from app.services.inventory_service import (
    canonical_items_json,
    item_to_response,
    request_hash,
    upsert_inventory_item,
)

router = APIRouter()


@router.get("")
def list_inventory(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = q.strip() if q else None
    where = "user_id = :user_id"
    parameters: dict[str, Any] = {"user_id": user_id, "limit": limit, "offset": offset}
    if query:
        where += " AND name ILIKE :query"
        parameters["query"] = f"%{query}%"
    total = db.execute(
        text(f"SELECT count(*) FROM inventory_items WHERE {where}"), parameters
    ).scalar_one()
    rows = fetch_all(
        db,
        f"""
        SELECT id, name, quantity, unit_code, expiry_date, ingredient_id
        FROM inventory_items
        WHERE {where}
        ORDER BY normalized_name, id
        LIMIT :limit OFFSET :offset
        """,
        parameters,
    )
    return {
        "items": [item_to_response(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_inventory_items(
    payload: InventoryBatchInput,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if idempotency_key is not None and not idempotency_key.strip():
        raise APIError(422, "VALIDATION_ERROR", "Idempotency-Key không hợp lệ.")
    if idempotency_key and len(idempotency_key) > 200:
        raise APIError(422, "VALIDATION_ERROR", "Idempotency-Key quá dài.")
    canonical = canonical_items_json(payload.items)
    fingerprint = request_hash(canonical)
    with db.begin():
        if idempotency_key:
            lock_key = f"inventory:{user_id}:{idempotency_key}"
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": lock_key},
            )
            previous = fetch_one(
                db,
                """
                SELECT request_hash, response_json
                FROM inventory_idempotency
                WHERE user_id = :user_id AND idempotency_key = :idempotency_key
                """,
                {"user_id": user_id, "idempotency_key": idempotency_key},
            )
            if previous:
                if previous["request_hash"] != fingerprint:
                    raise APIError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "Idempotency-Key đã dùng với payload khác.",
                    )
                response.status_code = status.HTTP_200_OK
                return previous["response_json"]
        items = [upsert_inventory_item(db, user_id, item) for item in payload.items]
        result = {"items": items}
        if idempotency_key:
            db.execute(
                text(
                    """
                    INSERT INTO inventory_idempotency (
                        user_id, idempotency_key, request_hash, response_json
                    ) VALUES (
                        :user_id, :idempotency_key, :request_hash,
                        CAST(:response_json AS JSONB)
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "idempotency_key": idempotency_key,
                    "request_hash": fingerprint,
                    "response_json": json.dumps(
                        result, ensure_ascii=False, default=str
                    ),
                },
            )
    return result


@router.patch("/{item_id}")
def patch_inventory_item(
    item_id: UUID,
    payload: InventoryItemPatch,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    fields = payload.model_fields_set
    with db.begin():
        existing = fetch_one(
            db,
            """
            SELECT id, name, quantity, unit_code, expiry_date, ingredient_id
            FROM inventory_items
            WHERE id = :item_id AND user_id = :user_id
            FOR UPDATE
            """,
            {"item_id": item_id, "user_id": user_id},
        )
        if existing is None:
            raise APIError(404, "NOT_FOUND", "Không tìm thấy nguyên liệu.")
        if "name" in fields and payload.name is None:
            raise APIError(422, "VALIDATION_ERROR", "Tên nguyên liệu không được null.")
        if "unit_code" in fields and payload.unit_code is None:
            raise APIError(422, "VALIDATION_ERROR", "Đơn vị không được null.")
        name = payload.name if "name" in fields else existing["name"]
        unit_code = payload.unit_code if "unit_code" in fields else existing["unit_code"]
        normalized_name = normalize_name(name)
        if not normalized_name:
            raise APIError(422, "VALIDATION_ERROR", "Tên nguyên liệu không hợp lệ.")
        ingredient = fetch_one(
            db,
            "SELECT id FROM ingredients WHERE normalized_name = :normalized_name",
            {"normalized_name": normalized_name},
        )
        quantity = payload.quantity if "quantity" in fields else existing["quantity"]
        expiry_date = (
            payload.expiry_date if "expiry_date" in fields else existing["expiry_date"]
        )
        row = fetch_one(
            db,
            """
            UPDATE inventory_items
            SET name = :name,
                normalized_name = :normalized_name,
                unit_code = :unit_code,
                quantity = :quantity,
                expiry_date = :expiry_date,
                ingredient_id = :ingredient_id,
                updated_at = now()
            WHERE id = :item_id AND user_id = :user_id
            RETURNING id, name, quantity, unit_code, expiry_date, ingredient_id
            """,
            {
                "name": name,
                "normalized_name": normalized_name,
                "unit_code": unit_code,
                "quantity": quantity,
                "expiry_date": expiry_date,
                "ingredient_id": ingredient["id"] if ingredient else None,
                "item_id": item_id,
                "user_id": user_id,
            },
        )
    return item_to_response(row)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inventory_item(
    item_id: UUID,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        deleted = db.execute(
            text(
                """
                DELETE FROM inventory_items
                WHERE id = :item_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"item_id": item_id, "user_id": user_id},
        ).first()
        if deleted is None:
            raise APIError(404, "NOT_FOUND", "Không tìm thấy nguyên liệu.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_all_inventory_items(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        db.execute(
            text("DELETE FROM inventory_items WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
