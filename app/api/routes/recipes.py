from math import ceil
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.errors import AI_ERROR_RESPONSES
from app.core.normalization import normalize_name
from app.db.session import get_db
from app.schemas.app_data import Ingredient
from app.schemas.recipe import Recipe, RecipeMatch
from app.services.db_utils import fetch_all
from app.services.recipe_generation import generate_recipes
from app.services.unit_aliases import to_base

router = APIRouter()


class MatchInput(BaseModel):
    inventory: list[Ingredient]
    portions: int = Field(ge=1, le=12)
    allergies: list[str]


class RecipeSourceInput(BaseModel):
    inventory: list[Ingredient]
    allergies: list[str]


class RecipeIdsInput(BaseModel):
    ids: list[UUID] = Field(max_length=21)


@router.post("/by-ids", response_model=list[Recipe])
def user_recipes_by_ids(
    payload: RecipeIdsInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if not payload.ids:
        return []
    rows = fetch_all(db, """
        SELECT recipe FROM user_recipes
        WHERE user_id = :user_id AND id = ANY(:ids)
        """, {"user_id": user_id, "ids": payload.ids})
    return [row["recipe"] for row in rows]


@router.post("/generated", response_model=list[Recipe], responses=AI_ERROR_RESPONSES)
def generated_recipes(
    payload: RecipeSourceInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return generate_recipes(db, user_id, payload.inventory, payload.allergies)


@router.post("/match", response_model=list[RecipeMatch], response_model_exclude_unset=True, responses=AI_ERROR_RESPONSES)
def match_recipes(
    payload: MatchInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    stock: dict[str, dict[str, Any]] = {}
    for item in payload.inventory:
        entry = stock.setdefault(normalize_name(item.name), {"units": {}, "unknown": False})
        if item.quantity is None:
            entry["unknown"] = True
        else:
            base = to_base(item.quantity, item.unit)
            entry["units"][base.dimension] = entry["units"].get(base.dimension, 0) + base.quantity
    matches = []
    for recipe in generate_recipes(db, user_id, payload.inventory, payload.allergies):
        scaled = []
        missing = []
        coverage = 0.0
        for item in recipe["items"]:
            quantity = ceil(item["quantity"] * payload.portions / recipe["servings"] - 1e-9)
            base = to_base(quantity, item["unit"])
            entry = stock.get(normalize_name(item["name"]))
            have = entry["units"].get(base.dimension, 0) if entry else 0
            coverage += min(1.0, have / base.quantity)
            scaled.append({"name": item["name"], "quantity": quantity, "unit": item["unit"]})
            if have < base.quantity:
                unit_base = to_base(1, item["unit"]).quantity
                gap = ceil((base.quantity - have) / unit_base - 1e-9)
                remainder = {"name": item["name"], "quantity": gap, "unit": item["unit"]}
                if entry and (entry["unknown"] or (entry["units"] and base.dimension not in entry["units"])):
                    remainder["check"] = True
                missing.append(remainder)
        matches.append({"recipe": recipe, "servings": payload.portions,
                        "items": scaled, "missing": missing,
                        "coverage": coverage / len(scaled)})
    return sorted(matches, key=lambda item: item["coverage"], reverse=True)


@router.get("")
def list_recipes(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200),
    _: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = q.strip() if q else None
    where = "is_active IS TRUE"
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if query:
        where += " AND title ILIKE :query"
        params["query"] = f"%{query}%"
    total = db.execute(
        text(f"SELECT count(*) FROM recipes WHERE {where}"), params
    ).scalar_one()
    recipes = fetch_all(
        db,
        f"""
        SELECT id, title, minutes, base_servings
        FROM recipes
        WHERE {where}
        ORDER BY id
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    ids = [recipe["id"] for recipe in recipes]
    ingredients = fetch_all(
        db,
        """
        SELECT ri.recipe_id, ri.ingredient_id, i.name,
               ri.quantity, ri.unit_code, ri.position
        FROM recipe_ingredients ri
        JOIN ingredients i ON i.id = ri.ingredient_id
        WHERE ri.recipe_id = ANY(:recipe_ids)
        ORDER BY ri.recipe_id, ri.position
        """,
        {"recipe_ids": ids},
    ) if ids else []
    steps = fetch_all(
        db,
        """
        SELECT recipe_id, position, text
        FROM recipe_steps
        WHERE recipe_id = ANY(:recipe_ids)
        ORDER BY recipe_id, position
        """,
        {"recipe_ids": ids},
    ) if ids else []
    items_by_recipe: dict[str, list[dict[str, Any]]] = {}
    for row in ingredients:
        items_by_recipe.setdefault(row["recipe_id"], []).append(
            {
                "ingredient_id": str(row["ingredient_id"]),
                "name": row["name"],
                "quantity": float(row["quantity"]),
                "unit_code": row["unit_code"],
            }
        )
    steps_by_recipe: dict[str, list[str]] = {}
    for row in steps:
        steps_by_recipe.setdefault(row["recipe_id"], []).append(row["text"])
    return {
        "items": [
            {
                "id": recipe["id"],
                "title": recipe["title"],
                "minutes": recipe["minutes"],
                "servings": recipe["base_servings"],
                "items": items_by_recipe.get(recipe["id"], []),
                "steps": steps_by_recipe.get(recipe["id"], []),
            }
            for recipe in recipes
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
