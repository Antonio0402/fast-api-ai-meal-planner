from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.errors import AI_ERROR_RESPONSES, APIError
from app.core.normalization import normalize_name
from app.db.session import get_db
from app.schemas.app_data import Ingredient, SavedPlan, ShoppingItem
from app.schemas.weekly_plan import (
    DraftPlanInput,
    GeneratePlanInput,
    PublishPlanInput,
    ShoppingChecksInput,
)
from app.services.db_utils import fetch_all, fetch_one
from app.services.recipe_generation import generate_recipes
from app.services.unit_aliases import from_base, to_base
from app.services.meal_plan_service import (
    EXPECTED_SLOT_KEYS,
    build_rotation_slots,
    build_shopping_items,
    generate_ai_slots,
    replace_draft_slots,
    slots_for_plan,
    validate_recipe_ids,
    validate_week_start,
)

router = APIRouter()


class GenerateFromInventoryInput(BaseModel):
    inventory: list[Ingredient]
    allergies: list[str]
    portions: int = Field(ge=1, le=12)
    weekStart: date | None = None


class BuildShoppingInput(BaseModel):
    plan: SavedPlan
    inventory: list[Ingredient]


@router.post("/generate-from-inventory", response_model=dict[str, str], responses=AI_ERROR_RESPONSES)
def generate_from_inventory(
    payload: GenerateFromInventoryInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    recipes = generate_recipes(db, user_id, payload.inventory, payload.allergies)
    if not recipes:
        raise APIError(422, "NO_RECIPES", "Hãy thêm nguyên liệu trước khi lập thực đơn.")
    return generate_ai_slots(recipes, portions=payload.portions, week_start=payload.weekStart)


@router.post("/shopping/build", response_model=list[ShoppingItem])
def build_shopping_from_plan(
    payload: BuildShoppingInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ids = list(set(payload.plan.slots.values()))
    try:
        recipe_uuids = [UUID(value) for value in ids]
    except ValueError as exc:
        raise APIError(422, "RECIPE_NOT_FOUND", "Kế hoạch chứa công thức không hợp lệ.") from exc
    rows = fetch_all(db, """
        SELECT recipe FROM user_recipes WHERE user_id = :user_id AND id = ANY(:ids)
        """, {"user_id": user_id, "ids": recipe_uuids}) if ids else []
    recipes = {row["recipe"]["id"]: row["recipe"] for row in rows}
    if set(ids) - set(recipes):
        raise APIError(422, "RECIPE_NOT_FOUND", "Kế hoạch chứa công thức không tồn tại.")
    needed: dict[str, dict[str, Any]] = {}
    for recipe_id in payload.plan.slots.values():
        recipe = recipes[recipe_id]
        for item in recipe["items"]:
            base = to_base(item["quantity"] * payload.plan.portions / recipe["servings"], item["unit"])
            key = f"{normalize_name(item['name'])}|{base.dimension}"
            entry = needed.setdefault(key, {"name": item["name"],
                                            "normalized_name": normalize_name(item["name"]),
                                            "dimension": base.dimension, "quantity": 0})
            entry["quantity"] += base.quantity
    stock: dict[str, dict[str, Any]] = {}
    for item in payload.inventory:
        entry = stock.setdefault(normalize_name(item.name), {"units": {}, "unknown": False})
        if item.quantity is None:
            entry["unknown"] = True
        else:
            base = to_base(item.quantity, item.unit)
            entry["units"][base.dimension] = entry["units"].get(base.dimension, 0) + base.quantity
    groups = {row["normalized_name"]: row["shopping_group"] for row in fetch_all(
        db,
        "SELECT normalized_name, shopping_group FROM ingredients WHERE normalized_name = ANY(:names)",
        {"names": [item["normalized_name"] for item in needed.values()]},
    )} if needed else {}
    output = []
    for key, item in needed.items():
        entry = stock.get(normalize_name(item["name"]))
        have = entry["units"].get(item["dimension"], 0) if entry else 0
        gap = max(0, item["quantity"] - have)
        check = bool(entry and (entry["unknown"] or (entry["units"] and item["dimension"] not in entry["units"])))
        if gap <= 1e-9 and not check:
            continue
        quantity, unit = from_base(gap, item["dimension"])
        output.append({"key": key, "name": item["name"], "unit": unit,
                       "toBuy": quantity, "group": groups.get(item["normalized_name"], "Khác"),
                       "check": check})
    return sorted(output, key=lambda item: item["name"])


def _published_slots(db: Session, revision_id: Any) -> dict[str, str]:
    rows = fetch_all(
        db,
        """
        SELECT day_index, meal_index, recipe_id
        FROM saved_plan_slots
        WHERE revision_id = :revision_id
        ORDER BY day_index, meal_index
        """,
        {"revision_id": revision_id},
    )
    return {
        f"{row['day_index']}-{row['meal_index']}": row["recipe_id"] for row in rows
    }


@router.get("")
def get_weekly_plan(
    week_start: date,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    validate_week_start(week_start)
    plan = fetch_one(
        db,
        """
        SELECT wp.id, wp.week_start, wp.draft_portions, wp.published_revision_id,
               revision.id AS revision_id, revision.version, revision.portions
        FROM weekly_plans wp
        LEFT JOIN saved_plan_revisions revision
          ON revision.id = wp.published_revision_id
        WHERE wp.user_id = :user_id AND wp.week_start = :week_start
        """,
        {"user_id": user_id, "week_start": week_start},
    )
    if plan is None:
        return {"plan": None}
    published = None
    if plan["revision_id"] is not None:
        published = {
            "id": str(plan["revision_id"]),
            "version": plan["version"],
            "portions": plan["portions"],
            "slots": _published_slots(db, plan["revision_id"]),
        }
    return {
        "plan": {
            "id": str(plan["id"]),
            "week_start": plan["week_start"].isoformat(),
            "draft_portions": plan["draft_portions"],
            "draft_slots": slots_for_plan(db, plan["id"]),
            "published_revision": published,
        }
    }


@router.post("/generate", dependencies=[Depends(get_current_user)])
def generate_weekly_plan(
    payload: GeneratePlanInput,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    validate_week_start(payload.week_start)
    recipes = fetch_all(
        db,
        "SELECT id, title FROM recipes WHERE is_active IS TRUE ORDER BY id",
    )
    if not recipes:
        raise APIError(422, "NO_ACTIVE_RECIPES", "Không có công thức đang hoạt động.")
    slots: dict[str, str]
    try:
        slots = generate_ai_slots(recipes)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(
            "Weekly plan generation used fallback (%s).", type(exc).__name__
        )
        slots = build_rotation_slots(recipes)
    if set(slots) != EXPECTED_SLOT_KEYS:
        raise APIError(500, "INTERNAL_ERROR", "Không thể tạo kế hoạch tuần.")
    return {
        "week_start": payload.week_start.isoformat(),
        "portions": payload.portions,
        "slots": slots,
    }


@router.put("/{week_start}/draft")
def put_weekly_draft(
    week_start: date,
    payload: DraftPlanInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    validate_week_start(week_start)
    with db.begin():
        validate_recipe_ids(db, payload.slots)
        plan = fetch_one(
            db,
            """
            INSERT INTO weekly_plans (user_id, week_start, draft_portions)
            VALUES (:user_id, :week_start, :portions)
            ON CONFLICT (user_id, week_start) DO UPDATE
            SET draft_portions = EXCLUDED.draft_portions, updated_at = now()
            RETURNING id
            """,
            {
                "user_id": user_id,
                "week_start": week_start,
                "portions": payload.portions,
            },
        )
        replace_draft_slots(db, plan["id"], payload.slots)
    return {
        "week_start": week_start.isoformat(),
        "portions": payload.portions,
        "slots": payload.slots,
    }


@router.put("/{week_start}")
def publish_weekly_plan(
    week_start: date,
    payload: PublishPlanInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    validate_week_start(week_start)
    with db.begin():
        validate_recipe_ids(db, payload.slots)
        db.execute(
            text(
                """
                INSERT INTO weekly_plans (user_id, week_start, draft_portions)
                VALUES (:user_id, :week_start, :portions)
                ON CONFLICT (user_id, week_start) DO NOTHING
                """
            ),
            {
                "user_id": user_id,
                "week_start": week_start,
                "portions": payload.portions,
            },
        )
        plan = fetch_one(
            db,
            """
            SELECT wp.id, revision.version
            FROM weekly_plans wp
            LEFT JOIN saved_plan_revisions revision
              ON revision.id = wp.published_revision_id
            WHERE wp.user_id = :user_id AND wp.week_start = :week_start
            FOR UPDATE OF wp
            """,
            {"user_id": user_id, "week_start": week_start},
        )
        current_version = plan["version"] if plan else None
        if current_version != payload.expected_version:
            raise APIError(
                409,
                "REVISION_CONFLICT",
                "Kế hoạch đã được cập nhật; hãy tải lại trước khi lưu.",
            )
        replace_draft_slots(db, plan["id"], payload.slots)
        db.execute(
            text(
                """
                UPDATE weekly_plans
                SET draft_portions = :portions, updated_at = now()
                WHERE id = :plan_id
                """
            ),
            {"portions": payload.portions, "plan_id": plan["id"]},
        )
        revision = fetch_one(
            db,
            """
            INSERT INTO saved_plan_revisions (weekly_plan_id, version, portions)
            VALUES (:plan_id, :version, :portions)
            RETURNING id, version
            """,
            {
                "plan_id": plan["id"],
                "version": (current_version or 0) + 1,
                "portions": payload.portions,
            },
        )
        for key, recipe_id in payload.slots.items():
            day_index, meal_index = (int(part) for part in key.split("-"))
            db.execute(
                text(
                    """
                    INSERT INTO saved_plan_slots (
                        revision_id, day_index, meal_index, recipe_id
                    ) VALUES (
                        :revision_id, :day_index, :meal_index, :recipe_id
                    )
                    """
                ),
                {
                    "revision_id": revision["id"],
                    "day_index": day_index,
                    "meal_index": meal_index,
                    "recipe_id": recipe_id,
                },
            )
        db.execute(
            text(
                """
                UPDATE weekly_plans
                SET published_revision_id = :revision_id, updated_at = now()
                WHERE id = :plan_id
                """
            ),
            {"revision_id": revision["id"], "plan_id": plan["id"]},
        )
    return {
        "id": str(plan["id"]),
        "week_start": week_start.isoformat(),
        "portions": payload.portions,
        "slots": payload.slots,
        "version": revision["version"],
        "revision_id": str(revision["id"]),
    }


@router.get("/{week_start}/shopping-list")
def get_shopping_list(
    week_start: date,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    validate_week_start(week_start)
    plan = fetch_one(
        db,
        """
        SELECT wp.published_revision_id, revision.version, revision.portions
        FROM weekly_plans wp
        JOIN saved_plan_revisions revision
          ON revision.id = wp.published_revision_id
        WHERE wp.user_id = :user_id AND wp.week_start = :week_start
        """,
        {"user_id": user_id, "week_start": week_start},
    )
    if plan is None:
        raise APIError(404, "PLAN_NOT_FOUND", "Chưa có kế hoạch tuần đã lưu.")
    items = build_shopping_items(
        db, user_id, plan["published_revision_id"], plan["portions"]
    )
    return {
        "revision_id": str(plan["published_revision_id"]),
        "version": plan["version"],
        "items": items,
        "total": len(items),
    }


@router.patch("/{week_start}/shopping-checks")
def patch_shopping_checks(
    week_start: date,
    payload: ShoppingChecksInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    validate_week_start(week_start)
    with db.begin():
        plan = fetch_one(
            db,
            """
            SELECT wp.published_revision_id, revision.portions
            FROM weekly_plans wp
            LEFT JOIN saved_plan_revisions revision
              ON revision.id = wp.published_revision_id
            WHERE wp.user_id = :user_id AND wp.week_start = :week_start
            FOR UPDATE OF wp
            """,
            {"user_id": user_id, "week_start": week_start},
        )
        if plan is None:
            raise APIError(404, "PLAN_NOT_FOUND", "Không tìm thấy kế hoạch tuần.")
        if plan["published_revision_id"] != payload.revision_id:
            raise APIError(409, "REVISION_CONFLICT", "Bản kế hoạch không còn đang mở.")
        visible_items = build_shopping_items(
            db, user_id, plan["published_revision_id"], plan["portions"]
        )
        allowed_keys = {
            (item["ingredient_id"], item["key"].split("|", 1)[1])
            for item in visible_items
        }
        submitted = [
            (str(item.ingredient_id), item.dimension_key) for item in payload.items
        ]
        if len(set(submitted)) != len(submitted):
            raise APIError(422, "VALIDATION_ERROR", "Mỗi món chỉ được gửi một lần.")
        if any(key not in allowed_keys for key in submitted):
            raise APIError(
                422,
                "INVALID_SHOPPING_ITEM",
                "Nguyên liệu hoặc đơn vị không có trong danh sách mua.",
            )
        for item in payload.items:
            db.execute(
                text(
                    """
                    INSERT INTO shopping_item_checks (
                        revision_id, ingredient_id, dimension_key, is_checked
                    ) VALUES (
                        :revision_id, :ingredient_id, :dimension_key, :is_checked
                    )
                    ON CONFLICT (revision_id, ingredient_id, dimension_key)
                    DO UPDATE SET is_checked = EXCLUDED.is_checked,
                                  updated_at = now()
                    """
                ),
                {
                    "revision_id": payload.revision_id,
                    "ingredient_id": item.ingredient_id,
                    "dimension_key": item.dimension_key,
                    "is_checked": item.is_checked,
                },
            )
    return {
        "revision_id": str(payload.revision_id),
        "items": [
            {
                "key": f"{item.ingredient_id}|{item.dimension_key}",
                "is_checked": item.is_checked,
            }
            for item in payload.items
        ],
    }
