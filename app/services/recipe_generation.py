"""Generate and cache user-owned recipes from confirmed inventory."""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.client import create_gemini_client
from app.ai.errors import meal_generation_error
from app.core.errors import APIError
from app.core.normalization import normalize_name
from app.schemas.app_data import Ingredient
from app.services.db_utils import fetch_all
from app.services.unit_aliases import to_base


class RecipeItem(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=40)


class GeneratedRecipe(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    minutes: int = Field(gt=0, le=240)
    servings: int = Field(ge=1, le=12)
    items: list[RecipeItem] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)


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
    source_hash = _source_hash(inventory, allergies)
    existing = fetch_all(db, """
        SELECT recipe FROM user_recipes
        WHERE user_id = :user_id AND source_hash = :source_hash ORDER BY created_at, id
        """, {"user_id": user_id, "source_hash": source_hash})
    if existing:
        return [row["recipe"] for row in existing]
    safe = _safe_inventory(db, inventory, allergies)
    if not safe:
        raise APIError(422, "NO_SAFE_INGREDIENTS", "Chưa có nguyên liệu được xác nhận an toàn với dị ứng.")
    allowed = {normalize_name(item.name): item for item in safe}
    prompt = (
        "Return ONLY a JSON array with 1 to 5 practical recipes. Each recipe has "
        "title, minutes, servings, items (name, quantity, unit), and steps. "
        "Use only ingredient names from this confirmed inventory. Do not add pantry items. "
        "Honor these allergies. Recipes are suggestions; do not claim allergy safety. "
        f"Inventory: {json.dumps([item.model_dump(exclude_none=True) for item in safe], ensure_ascii=False)}. "
        f"Allergies: {json.dumps(allergies, ensure_ascii=False)}"
    )
    try:
        raw = json.loads(create_gemini_client().generate_text(prompt))
        if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
            raise ValueError("Invalid recipe count")
        parsed = [GeneratedRecipe.model_validate(item) for item in raw]
    except APIError:
        raise
    except Exception as exc:
        raise meal_generation_error(exc) from exc
    for recipe in parsed:
        for item in recipe.items:
            original = allowed.get(normalize_name(item.name))
            if original is None or to_base(1, original.unit).dimension != to_base(1, item.unit).dimension:
                raise APIError(503, "AI_OUTPUT_INVALID", "AI dùng nguyên liệu ngoài kho đã xác nhận.")
    generated = [{"id": str(uuid4()), **recipe.model_dump()} for recipe in parsed]
    db.commit()
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": f"recipes:{user_id}:{source_hash}"})
        existing = fetch_all(db, """
            SELECT recipe FROM user_recipes
            WHERE user_id = :user_id AND source_hash = :source_hash ORDER BY created_at, id
            """, {"user_id": user_id, "source_hash": source_hash})
        if existing:
            return [row["recipe"] for row in existing]
        for recipe in generated:
            db.execute(text("""
                INSERT INTO user_recipes (user_id, id, recipe, source_hash)
                VALUES (:user_id, :id, CAST(:recipe AS jsonb), :source_hash)
                """), {"user_id": user_id, "id": UUID(recipe["id"]),
                       "recipe": json.dumps(recipe, ensure_ascii=False),
                       "source_hash": source_hash})
    return generated
