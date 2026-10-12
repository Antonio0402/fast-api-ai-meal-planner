from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.core.normalization import normalize_name
from app.schemas.inventory import InventoryItemInput
from app.services.db_utils import fetch_one


def item_to_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "quantity": float(row["quantity"]) if row["quantity"] is not None else None,
        "unit_code": row["unit_code"],
        "expiry_date": row["expiry_date"],
        "ingredient_id": (
            str(row["ingredient_id"]) if row["ingredient_id"] is not None else None
        ),
    }


def upsert_inventory_item(
    db: Session,
    user_id: str,
    item: InventoryItemInput,
    ingredient_id: str | None = None,
) -> dict[str, Any]:
    normalized_name = normalize_name(item.name)
    if not normalized_name:
        raise APIError(422, "VALIDATION_ERROR", "Tên nguyên liệu không hợp lệ.")
    if ingredient_id is None:
        ingredient = fetch_one(
            db,
            "SELECT id FROM ingredients WHERE normalized_name = :normalized_name",
            {"normalized_name": normalized_name},
        )
        ingredient_id = str(ingredient["id"]) if ingredient else None
    existing = fetch_one(
        db,
        """
        SELECT id, ingredient_id, name, quantity, unit_code, expiry_date
        FROM inventory_items
        WHERE user_id = :user_id
          AND normalized_name = :normalized_name
          AND unit_code = :unit_code
        FOR UPDATE
        """,
        {
            "user_id": user_id,
            "normalized_name": normalized_name,
            "unit_code": item.unit_code,
        },
    )
    if existing:
        old_quantity = existing["quantity"]
        if item.quantity is None:
            quantity = old_quantity
        elif old_quantity is None:
            quantity = item.quantity
        else:
            quantity = Decimal(old_quantity) + item.quantity
        expiry_date = item.expiry_date or existing["expiry_date"]
        row = fetch_one(
            db,
            """
            UPDATE inventory_items
            SET quantity = :quantity,
                expiry_date = :expiry_date,
                ingredient_id = COALESCE(:ingredient_id, ingredient_id),
                updated_at = now()
            WHERE id = :item_id AND user_id = :user_id
            RETURNING id, name, quantity, unit_code, expiry_date, ingredient_id
            """,
            {
                "quantity": quantity,
                "expiry_date": expiry_date,
                "ingredient_id": ingredient_id,
                "item_id": existing["id"],
                "user_id": user_id,
            },
        )
    else:
        row = fetch_one(
            db,
            """
            INSERT INTO inventory_items (
                user_id, ingredient_id, name, normalized_name,
                quantity, unit_code, expiry_date
            )
            VALUES (
                :user_id, :ingredient_id, :name, :normalized_name,
                :quantity, :unit_code, :expiry_date
            )
            RETURNING id, name, quantity, unit_code, expiry_date, ingredient_id
            """,
            {
                "user_id": user_id,
                "ingredient_id": ingredient_id,
                "name": item.name,
                "normalized_name": normalized_name,
                "quantity": item.quantity,
                "unit_code": item.unit_code,
                "expiry_date": item.expiry_date,
            },
        )
    if row is None:
        raise APIError(500, "INTERNAL_ERROR", "Không thể lưu nguyên liệu.")
    return item_to_response(row)


def canonical_items_json(items: list[InventoryItemInput]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def request_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
