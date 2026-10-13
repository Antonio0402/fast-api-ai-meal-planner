"""Generate and cache user-owned recipes from confirmed inventory."""

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.client import create_gemini_client
from app.ai.errors import meal_generation_error
from app.core.errors import APIError
from app.core.normalization import normalize_name
from app.schemas.app_data import Ingredient
from app.schemas.recipe import GeneratedRecipe, Recipe
from app.services.db_utils import fetch_all
from app.services.unit_aliases import to_base

RECIPE_BATCH = TypeAdapter(Annotated[list[GeneratedRecipe], Field(min_length=1, max_length=5)])


def _validate_inventory(recipes: list[GeneratedRecipe], allowed: set[tuple[str, str]]) -> None:
    for recipe in recipes:
        for item in recipe.items:
            if (normalize_name(item.name), to_base(1, item.unit).dimension) not in allowed:
                raise ValueError('AI dùng nguyên liệu hoặc đơn vị ngoài kho đã xác nhận.')


def _cached_recipes(rows: list[dict[str, Any]], allowed: set[tuple[str, str]]) -> list[dict[str, Any]]:
    if not 1 <= len(rows) <= 5:
        return []
    try:
        recipes = [Recipe.model_validate(row['recipe']) for row in rows]
        for recipe in recipes:
            UUID(recipe.id)
        _validate_inventory(recipes, allowed)
        return [recipe.model_dump() for recipe in recipes]
    except (ValueError, KeyError, TypeError):
        # Cache cũ phải vượt qua cùng validation trước khi trả về mobile.
        return []


def _source_hash(inventory: list[Ingredient], allergies: list[str]) -> str:
    source = {
        "inventory": sorted(
            [{"name": normalize_name(item.name), "quantity": item.quantity,
              "unit": item.unit} for item in inventory],
            key=lambda item: (item["name"], item["unit"]),
        ),
        "allergies": sorted(set(allergies)),
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def _safe_inventory(db: Session, inventory: list[Ingredient], allergies: list[str]) -> list[Ingredient]:
    if not allergies:
        return inventory
    names = [normalize_name(item.name) for item in inventory]
    rows = fetch_all(db, """
        SELECT i.normalized_name, array_remove(array_agg(a.code), NULL) AS allergens
        FROM ingredients i
        LEFT JOIN ingredient_allergens ia ON ia.ingredient_id = i.id
        LEFT JOIN allergens a ON a.id = ia.allergen_id
        WHERE i.normalized_name = ANY(:names)
        GROUP BY i.id
        """, {"names": names})
    known = {row["normalized_name"]: set(row["allergens"]) for row in rows}
    return [item for item in inventory
            if normalize_name(item.name) in known
            and not known[normalize_name(item.name)] & set(allergies)]


def list_generated_recipes(db: Session, user_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(db, """
        SELECT recipe FROM user_recipes WHERE user_id = :user_id
        ORDER BY created_at DESC, id LIMIT 200
        """, {"user_id": user_id})
    return [row["recipe"] for row in rows]


def generate_recipes(
    db: Session, user_id: str, inventory: list[Ingredient], allergies: list[str]
) -> list[dict[str, Any]]:
    if not inventory:
        return []
    safe = _safe_inventory(db, inventory, allergies)
    if not safe:
        raise APIError(422, "NO_SAFE_INGREDIENTS", "Chưa có nguyên liệu được xác nhận an toàn với dị ứng.")
    allowed = {(normalize_name(item.name), to_base(1, item.unit).dimension) for item in safe}
    source_hash = _source_hash(inventory, allergies)
    existing = fetch_all(db, """
        SELECT recipe FROM user_recipes
        WHERE user_id = :user_id AND source_hash = :source_hash ORDER BY created_at, id
        """, {"user_id": user_id, "source_hash": source_hash})
    cached = _cached_recipes(existing, allowed)
    if cached:
        return cached
    # Kết thúc transaction đọc trước khi chờ dịch vụ AI.
    db.commit()
    prompt = (
        "Return ONLY a JSON array with 1 to 5 practical recipes. Each recipe has "
        "title, minutes, servings, items (name, quantity, unit), and steps. "
        "Use only ingredient names from this confirmed inventory. Do not add pantry items. "
        "Write titles and cooking steps in Vietnamese. Preserve the inventory ingredient names. "
        "List each ingredient once with its total quantity and a compatible unit. "
        "Steps must refer to the ingredient list rather than repeat fixed quantities, "
        "because the app scales quantities for the selected servings. "
        "Honor these allergies. Recipes are suggestions; do not claim allergy safety. "
        f"Inventory: {json.dumps([item.model_dump(exclude_none=True) for item in safe], ensure_ascii=False)}. "
        f"Allergies: {json.dumps(allergies, ensure_ascii=False)}"
    )
    try:
        raw = create_gemini_client().generate_text(prompt, response_schema=RECIPE_BATCH.json_schema())
        parsed = RECIPE_BATCH.validate_json(raw)
        _validate_inventory(parsed, allowed)
    except APIError:
        raise
    except Exception as exc:
        raise meal_generation_error(exc) from exc
    generated = [{"id": str(uuid4()), **recipe.model_dump()} for recipe in parsed]
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": f"recipes:{user_id}:{source_hash}"})
        existing = fetch_all(db, """
            SELECT recipe FROM user_recipes
            WHERE user_id = :user_id AND source_hash = :source_hash ORDER BY created_at, id
            """, {"user_id": user_id, "source_hash": source_hash})
        cached = _cached_recipes(existing, allowed)
        if cached:
            return cached
        if existing:
            # Giữ ID và nội dung cũ cho thực đơn đã lưu, chỉ loại khỏi cache hiện hành.
            db.execute(text("""
                UPDATE user_recipes SET source_hash = :invalid_hash
                WHERE user_id = :user_id AND source_hash = :source_hash
                """), {"user_id": user_id, "source_hash": source_hash,
                       "invalid_hash": f"invalid:{source_hash}"})
        for recipe in generated:
            db.execute(text("""
                INSERT INTO user_recipes (user_id, id, recipe, source_hash)
                VALUES (:user_id, :id, CAST(:recipe AS jsonb), :source_hash)
                """), {"user_id": user_id, "id": UUID(recipe["id"]),
                       "recipe": json.dumps(recipe, ensure_ascii=False),
                       "source_hash": source_hash})
    return generated
