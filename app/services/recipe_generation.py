"""Generate and cache user-owned recipes from confirmed inventory."""

import hashlib
import json
import logging
import sys
import traceback
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
from app.services.allergy import expand_allergy_terms, find_allergy_term, normalize_allergies
from app.services.db_utils import fetch_all
from app.services.unit_aliases import to_base

logger = logging.getLogger(__name__)

MIN_RECIPES = 7
MAX_RECIPES = 21
FULL_PLAN_CAPACITY_RATIO = 0.6
RECIPE_BATCH = TypeAdapter(
    Annotated[list[GeneratedRecipe], Field(min_length=MIN_RECIPES, max_length=MAX_RECIPES)]
)


COMMON_PANTRY_SEASONINGS = {
    "muoi", "duong", "bot ngot", "hat nem", "mi chinh", "tieu",
    "dau an", "nuoc mam", "nuoc tuong", "xi dau", "nuoc",
    "toi", "hanh tim", "hanh la", "gung", "ot", "chanh",
    "giam", "dau hao", "sot ca chua", "tuong ca", "tuong ot",
}


def _clean_json_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _extract_recipes_data(raw: str) -> list[Any]:
    cleaned = _clean_json_text(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start_bracket = cleaned.find("[")
        end_bracket = cleaned.rfind("]")
        start_brace = cleaned.find("{")
        end_brace = cleaned.rfind("}")
        if start_bracket != -1 and end_bracket != -1 and (start_brace == -1 or start_bracket < start_brace):
            data = json.loads(cleaned[start_bracket:end_bracket + 1])
        elif start_brace != -1 and end_brace != -1:
            data = json.loads(cleaned[start_brace:end_brace + 1])
        else:
            raise

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("recipes", "data", "items", "meal_plan", "result"):
            if isinstance(data.get(key), list):
                return data[key]
        for val in data.values():
            if isinstance(val, list):
                return val
    raise ValueError("AI không trả về danh sách công thức dạng mảng.")


def _align_inventory_names(
    recipes: list[GeneratedRecipe],
    safe: list[Ingredient],
    allowed: set[tuple[str, str]],
) -> None:
    for recipe in recipes:
        changed = False
        for item in recipe.items:
            norm_item = normalize_name(item.name)
            dim = to_base(1, item.unit).dimension
            if (norm_item, dim) in allowed:
                continue
            for inv in safe:
                norm_inv = normalize_name(inv.name)
                inv_dim = to_base(1, inv.unit).dimension
                if inv_dim != dim:
                    continue
                if (
                    norm_item.startswith(f"{norm_inv} ")
                    or norm_item.endswith(f" {norm_inv}")
                    or norm_inv.startswith(f"{norm_item} ")
                    or norm_inv.endswith(f" {norm_item}")
                    or f" {norm_inv} " in f" {norm_item} "
                    or f" {norm_item} " in f" {norm_inv} "
                ):
                    item.name = inv.name
                    changed = True
                    break
        if changed:
            recipe.merge_ingredients()


def _filter_pantry_items(
    recipes: list[GeneratedRecipe],
    allowed_names: set[str],
) -> None:
    for recipe in recipes:
        has_allowed_item = any(normalize_name(item.name) in allowed_names for item in recipe.items)
        if has_allowed_item:
            filtered = [
                item for item in recipe.items
                if normalize_name(item.name) in allowed_names
                or normalize_name(item.name) not in COMMON_PANTRY_SEASONINGS
            ]
            if filtered:
                recipe.items = filtered


def _validate_inventory(recipes: list[GeneratedRecipe] | list[Recipe], allowed: set[tuple[str, str]]) -> None:
    for recipe in recipes:
        if not recipe.items:
            raise ValueError('Công thức không chứa nguyên liệu hợp lệ.')
        valid_count = 0
        for item in recipe.items:
            norm_name = normalize_name(item.name)
            dim = to_base(1, item.unit).dimension
            if (norm_name, dim) in allowed:
                valid_count += 1
            else:
                raise ValueError(
                    f"AI dùng nguyên liệu hoặc đơn vị ngoài kho đã xác nhận: {item.name} ({item.unit})"
                )
        if valid_count == 0:
            raise ValueError('Công thức không chứa nguyên liệu nào từ kho đã xác nhận.')


def _validate_allergies(recipes: list[GeneratedRecipe], terms: list[str]) -> None:
    if not terms:
        return
    for recipe in recipes:
        for value in (recipe.title, *(item.name for item in recipe.items), *recipe.steps):
            if find_allergy_term(value, terms):
                raise ValueError('AI tạo công thức chứa thành phần gây dị ứng.')


def _cached_recipes(
    rows: list[dict[str, Any]], allowed: set[tuple[str, str]], terms: list[str]
) -> list[dict[str, Any]]:
    if not MIN_RECIPES <= len(rows) <= MAX_RECIPES:
        return []
    try:
        recipes = [Recipe.model_validate(row['recipe']) for row in rows]
        for recipe in recipes:
            UUID(recipe.id)
        _validate_inventory(recipes, allowed)
        _validate_allergies(recipes, terms)
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
        "allergies": sorted(set(normalize_allergies(allergies))),
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def _safe_inventory(db: Session, inventory: list[Ingredient], allergies: list[str]) -> list[Ingredient]:
    terms = expand_allergy_terms(allergies)
    if not terms:
        return inventory
    codes = {normalize_name(allergy) for allergy in normalize_allergies(allergies)}
    names = [normalize_name(item.name) for item in inventory]
    rows = fetch_all(db, """
        SELECT i.normalized_name,
               array_remove(array_agg(a.code) || array_agg(a.name), NULL) AS allergens
        FROM ingredients i
        LEFT JOIN ingredient_allergens ia ON ia.ingredient_id = i.id
        LEFT JOIN allergens a ON a.id = ia.allergen_id
        WHERE i.normalized_name = ANY(:names)
        GROUP BY i.id
        """, {"names": names})
    known = {row["normalized_name"]: {normalize_name(value) for value in row["allergens"]}
             for row in rows}
    return [item for item in inventory
            if not find_allergy_term(item.name, terms)
            and not known.get(normalize_name(item.name), set()) & codes]


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
    terms = expand_allergy_terms(allergies)
    allowed = {(normalize_name(item.name), to_base(1, item.unit).dimension) for item in safe}
    allowed_names = {name for name, _ in allowed}
    print(
        f"[RECIPE_GEN] Starting generation for user={user_id} | "
        f"Inventory={len(inventory)} items ({[i.name for i in inventory]}) | "
        f"Safe={len(safe)} items ({[i.name for i in safe]}) | "
        f"Allergies={allergies}",
        file=sys.stderr,
        flush=True,
    )
    source_hash = _source_hash(inventory, allergies)
    existing = fetch_all(db, """
        SELECT recipe FROM user_recipes
        WHERE user_id = :user_id AND source_hash = :source_hash ORDER BY created_at, id
        """, {"user_id": user_id, "source_hash": source_hash})
    cached = _cached_recipes(existing, allowed, terms)
    if cached:
        print(
            f"[RECIPE_GEN] Returning {len(cached)} cached recipes for user={user_id}",
            file=sys.stderr,
            flush=True,
        )
        return cached
    # Kết thúc transaction đọc trước khi chờ dịch vụ AI.
    db.commit()
    allergy_rule = (
        "MANDATORY ALLERGY RULE: The user is allergic to the items in Allergies. "
        "Always strictly respect this allergy_rule with highest priority: never use, suggest, "
        "substitute, or mention any item in Allergies, any ingredient in AvoidTerms, "
        "or any ingredient derived from them, including in recipe titles, ingredient items, "
        "and cooking steps. Verify every recipe against Allergies and AvoidTerms before returning. "
        "Recipes are suggestions; do not claim allergy safety. "
        f"Allergies: {json.dumps(normalize_allergies(allergies), ensure_ascii=False)}. "
        f"AvoidTerms: {json.dumps(terms, ensure_ascii=False)}. "
    ) if terms else (
        "Allergy rule: No specific allergens declared, use only the confirmed safe inventory. "
    )
    has_full_week_capacity = len(safe) > (MAX_RECIPES * FULL_PLAN_CAPACITY_RATIO)
    capacity_rule = (
        f"The current confirmed inventory has {len(safe)} safe ingredients, which is more than "
        f"60% capacity for creating {MAX_RECIPES} recipes: you MUST try to create a full set of "
        f"{MAX_RECIPES} different practical recipes (3 meals/day for 7 days). "
        if has_full_week_capacity else
        f"The current confirmed inventory has {len(safe)} safe ingredients: create at least "
        f"{MIN_RECIPES} practical recipes for rotating on the weekplan (at least 1 recipe a day), "
        f"and always try to create up to {MAX_RECIPES} different recipes if the inventory "
        f"ingredients have more than 60% capacity to support {MAX_RECIPES} different recipes. "
    )
    prompt = (
        f"Return ONLY a JSON array with {MIN_RECIPES} to {MAX_RECIPES} different practical recipes "
        f"for rotating on a 7-day weekplan (minimum {MIN_RECIPES} practical recipes, which is at "
        f"least 1 recipe a day; always try to create a full {MAX_RECIPES} different recipes when "
        "the current number of inventory ingredients has more than 60% capacity for creating "
        f"full {MAX_RECIPES} different recipes). "
        f"{capacity_rule}"
        f"Always respect the allergy_rule in every recipe. {allergy_rule}"
        "Each recipe must be distinct and have title, minutes, servings, items (name, quantity, unit), and steps. "
        "Use only ingredient names from this confirmed inventory. Do not add pantry items. "
        "Write titles and cooking steps in Vietnamese. Preserve the inventory ingredient names. "
        "List each ingredient once with its total quantity and a compatible unit. "
        "Steps must refer to the ingredient list rather than repeat fixed quantities, "
        "because the app scales quantities for the selected servings. "
        f"Inventory: {json.dumps([item.model_dump(exclude_none=True) for item in safe], ensure_ascii=False)}."
    )
    try:
        print(f"[RECIPE_GEN] Requesting Gemini for user={user_id}...", file=sys.stderr, flush=True)
        raw = create_gemini_client().generate_text(prompt, response_schema=RECIPE_BATCH.json_schema())
        print(
            f"[RECIPE_GEN] Gemini raw response length: {len(raw) if raw else 0}",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[RECIPE_GEN] Gemini preview: {(raw[:250] if raw else 'None')!r}",
            file=sys.stderr,
            flush=True,
        )
        recipes_data = _extract_recipes_data(raw)
        print(
            f"[RECIPE_GEN] Extracted {len(recipes_data)} recipe items from payload",
            file=sys.stderr,
            flush=True,
        )
        parsed = RECIPE_BATCH.validate_python(recipes_data)
        _align_inventory_names(parsed, safe, allowed)
        _filter_pantry_items(parsed, allowed_names)
        _validate_inventory(parsed, allowed)
        _validate_allergies(parsed, terms)
        print(
            f"[RECIPE_GEN] Successfully validated {len(parsed)} recipes",
            file=sys.stderr,
            flush=True,
        )
    except APIError:
        raise
    except Exception as exc:
        print(
            f"[RECIPE_GEN ERROR] Recipe generation failed: {exc}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc(file=sys.stderr)
        print(
            f"[RECIPE_GEN ERROR] Raw response was: {raw if 'raw' in locals() else 'None'}",
            file=sys.stderr,
            flush=True,
        )
        logger.error(
            "Recipe generation validation failed: %s | Raw: %.500s",
            exc,
            raw if "raw" in locals() else "None",
        )
        raise meal_generation_error(exc) from exc
    generated = [{"id": str(uuid4()), **recipe.model_dump()} for recipe in parsed]
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                   {"key": f"recipes:{user_id}:{source_hash}"})
        existing = fetch_all(db, """
            SELECT recipe FROM user_recipes
            WHERE user_id = :user_id AND source_hash = :source_hash ORDER BY created_at, id
            """, {"user_id": user_id, "source_hash": source_hash})
        cached = _cached_recipes(existing, allowed, terms)
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
