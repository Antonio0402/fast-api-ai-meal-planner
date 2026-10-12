from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.client import create_gemini_client
from app.ai.errors import meal_generation_error
from app.core.errors import APIError
from app.core.normalization import normalize_name
from app.services.db_utils import fetch_all
from app.services.unit_aliases import from_base, to_base

logger = logging.getLogger(__name__)

EXPECTED_SLOT_KEYS = {
    f"{day}-{meal}" for day in range(7) for meal in range(3)
}


def validate_week_start(week_start: date) -> None:
    if week_start.weekday() != 0:
        raise APIError(422, "VALIDATION_ERROR", "week_start phải là thứ Hai.")


def validate_recipe_ids(db: Session, slots: dict[str, str]) -> None:
    if not slots:
        return
    recipe_ids = list(set(slots.values()))
    active_ids = set(
        db.execute(
            text(
                "SELECT id FROM recipes WHERE is_active IS TRUE AND id = ANY(:recipe_ids)"
            ),
            {"recipe_ids": recipe_ids},
        ).scalars()
    )
    if active_ids != set(recipe_ids):
        raise APIError(404, "RECIPE_NOT_FOUND", "Công thức không tồn tại hoặc đã bị ẩn.")


def slots_for_plan(db: Session, plan_id: Any) -> dict[str, str]:
    rows = fetch_all(
        db,
        """
        SELECT day_index, meal_index, recipe_id
        FROM weekly_plan_slots
        WHERE weekly_plan_id = :plan_id
        ORDER BY day_index, meal_index
        """,
        {"plan_id": plan_id},
    )
    return {
        f"{row['day_index']}-{row['meal_index']}": row["recipe_id"] for row in rows
    }


def replace_draft_slots(db: Session, plan_id: Any, slots: dict[str, str]) -> None:
    db.execute(
        text("DELETE FROM weekly_plan_slots WHERE weekly_plan_id = :plan_id"),
        {"plan_id": plan_id},
    )
    for key, recipe_id in slots.items():
        day_index, meal_index = (int(part) for part in key.split("-"))
        db.execute(
            text(
                """
                INSERT INTO weekly_plan_slots (
                    weekly_plan_id, day_index, meal_index, recipe_id
                ) VALUES (:plan_id, :day_index, :meal_index, :recipe_id)
                """
            ),
            {
                "plan_id": plan_id,
                "day_index": day_index,
                "meal_index": meal_index,
                "recipe_id": recipe_id,
            },
        )


def generate_ai_slots(
    recipes: list[dict[str, Any]], *, portions: int | None = None,
    week_start: date | None = None,
) -> dict[str, str]:
    catalog = [
        {"id": row["id"], "title": row["title"],
         "ingredients": [item["name"] for item in row.get("items", [])]}
        for row in recipes
    ]
    prompt = (
        "Plan seven days of breakfast (meal 0), lunch (meal 1), and dinner (meal 2). "
        "Return only JSON with one 'slots' object. Its keys must be exactly: "
        f"{json.dumps(sorted(EXPECTED_SLOT_KEYS))}. "
        "Each value must be an id from the catalog. Distribute available recipes "
        "across the week, avoid repeating a recipe in adjacent meals when possible, "
        "and use the title and ingredients to choose suitable meals. "
        f"Portions: {portions}. Week start: {week_start}. Catalog: "
        f"{json.dumps(catalog, ensure_ascii=False)}"
    )
    try:
        result = json.loads(create_gemini_client().generate_text(prompt))
    except APIError:
        raise
    except Exception as exc:
        raise meal_generation_error(exc) from exc
    if not isinstance(result, dict) or set(result) != {"slots"}:
        raise APIError(502, "AI_OUTPUT_INVALID", "AI chưa tạo được thực đơn hợp lệ.")
    slots = result["slots"]
    recipe_ids = {recipe["id"] for recipe in recipes}
    if (
        not isinstance(slots, dict)
        or set(slots) != EXPECTED_SLOT_KEYS
        or not all(
            isinstance(recipe_id, str)
            and recipe_id in recipe_ids
            for recipe_id in slots.values()
        )
    ):
        raise APIError(502, "AI_OUTPUT_INVALID", "AI chưa tạo được thực đơn hợp lệ.")
    return slots


def build_rotation_slots(recipes: list[dict[str, Any]]) -> dict[str, str]:
    return {
        f"{day}-{meal}": recipes[(day * 3 + meal) % len(recipes)]["id"]
        for day in range(7)
        for meal in range(3)
    }


def build_shopping_items(
    db: Session,
    user_id: str,
    revision_id: Any,
    portions: int,
) -> list[dict[str, Any]]:
    recipe_rows = fetch_all(
        db,
        """
        SELECT ri.recipe_id, ri.ingredient_id, i.name, i.normalized_name,
               i.shopping_group, ri.quantity, ri.unit_code, r.base_servings
        FROM saved_plan_slots s
        JOIN recipes r ON r.id = s.recipe_id
        JOIN recipe_ingredients ri ON ri.recipe_id = r.id
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE s.revision_id = :revision_id
        ORDER BY s.day_index, s.meal_index, ri.position
        """,
        {"revision_id": revision_id},
    )
    needed: dict[str, dict[str, Any]] = {}
    for row in recipe_rows:
        base = to_base(
            float(row["quantity"]) * portions / row["base_servings"],
            row["unit_code"],
        )
        ingredient_id = str(row["ingredient_id"])
        key = f"{ingredient_id}|{base.dimension}"
        current = needed.get(key)
        if current is None:
            needed[key] = {
                "ingredient_id": ingredient_id,
                "name": row["name"],
                "normalized_name": row["normalized_name"],
                "group": row["shopping_group"],
                "dimension": base.dimension,
                "quantity": base.quantity,
            }
        else:
            current["quantity"] += base.quantity

    inventory = fetch_all(
        db,
        """
        SELECT ingredient_id, normalized_name, quantity, unit_code
        FROM inventory_items
        WHERE user_id = :user_id
        """,
        {"user_id": user_id},
    )
    checks = fetch_all(
        db,
        """
        SELECT ingredient_id, dimension_key, is_checked
        FROM shopping_item_checks
        WHERE revision_id = :revision_id
        """,
        {"revision_id": revision_id},
    )
    checked_by_key = {
        f"{row['ingredient_id']}|{row['dimension_key']}": row["is_checked"]
        for row in checks
    }
    result: list[dict[str, Any]] = []
    for key, item in needed.items():
        stock_by_dimension: dict[str, float] = {}
        has_unknown_quantity = False
        for stock in inventory:
            matches_catalog = (
                str(stock["ingredient_id"]) == item["ingredient_id"]
                if stock["ingredient_id"] is not None
                else stock["normalized_name"] == item["normalized_name"]
            )
            if not matches_catalog:
                continue
            if stock["quantity"] is None:
                has_unknown_quantity = True
                continue
            converted = to_base(float(stock["quantity"]), stock["unit_code"])
            stock_by_dimension[converted.dimension] = (
                stock_by_dimension.get(converted.dimension, 0.0) + converted.quantity
            )
        dimension = item["dimension"]
        needs_check = has_unknown_quantity or (
            bool(stock_by_dimension) and dimension not in stock_by_dimension
        )
        to_buy = max(
            0.0,
            item["quantity"] - stock_by_dimension.get(dimension, 0.0),
        )
        if to_buy <= 1e-9 and not needs_check:
            continue
        quantity, unit = from_base(to_buy, dimension)
        result.append(
            {
                "key": key,
                "ingredient_id": item["ingredient_id"],
                "name": item["name"],
                "unit": unit,
                "to_buy": quantity,
                "group": item["group"],
                "needs_check": needs_check,
                "is_checked": checked_by_key.get(key, False),
            }
        )
    result.sort(
        key=lambda item: (
            normalize_name(item["group"]),
            normalize_name(item["name"]),
            item["key"],
        )
    )
    return result
