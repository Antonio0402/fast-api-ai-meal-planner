"""Scan module copied from GeminiAgentMealPLanner/ai_module/scan.py on 2026-10-11."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping, Protocol


class ScanIngredientsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScanModelClient(Protocol):
    def scan_ingredients(
        self, *, image_bytes: bytes, mime_type: str, prompt: str
    ) -> Mapping[str, Any] | str:
        ...


MAX_CANDIDATES = 20

SCAN_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "image_quality": {"type": "string", "enum": ["ok", "blurry", "no_food"]},
        "ingredients": {
            "type": "array",
            "maxItems": MAX_CANDIDATES,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Tên nguyên liệu tiếng Việt, tối đa 80 ký tự."},
                    "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                },
                "required": ["name", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["image_quality", "ingredients"],
    "additionalProperties": False,
}

SCAN_PROMPT = (
    'Return only JSON with shape {"image_quality":"ok|blurry|no_food",'
    '"ingredients":[{"name":"...","confidence":null}]}. '
    f"List at most {MAX_CANDIDATES} clearly recognized food ingredients, prioritizing "
    "the clearest items when there are more. Write ingredient names in "
    "Vietnamese with correct diacritics. Do not infer quantity, unit, freshness, "
    "hidden items, IDs, comments, or extra fields. Confidence may be null; "
    "otherwise it must be a number from 0 to 1. Use image_quality=blurry only "
    "when the image is visibly too blurry to identify food, and no_food when "
    "there is no visible food; in that case return an empty ingredients array."
)


def scan_ingredients(
    *,
    image_bytes: bytes,
    mime_type: str,
    model_client: ScanModelClient,
) -> dict[str, Any]:
    if not image_bytes or not mime_type:
        raise ScanIngredientsError("INVALID_INPUT", "Invalid image input.")
    raw = model_client.scan_ingredients(
        image_bytes=image_bytes,
        mime_type=mime_type,
        prompt=SCAN_PROMPT,
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScanIngredientsError(
                "OUTPUT_SCHEMA_INVALID", "Model output is not valid JSON."
            ) from exc
    if not isinstance(raw, Mapping):
        raise ScanIngredientsError(
            "OUTPUT_SCHEMA_INVALID", "Model output must be a JSON object."
        )
    if set(raw) - {"ingredients", "image_quality"}:
        raise ScanIngredientsError(
            "OUTPUT_SCHEMA_INVALID", "Model output contains unknown fields."
        )
    quality = raw.get("image_quality")
    if quality is not None and quality not in {"ok", "blurry", "no_food"}:
        raise ScanIngredientsError(
            "OUTPUT_SCHEMA_INVALID", "Image quality value is invalid."
        )
    ingredients = raw.get("ingredients")
    if not isinstance(ingredients, list) or len(ingredients) > MAX_CANDIDATES:
        raise ScanIngredientsError(
            "OUTPUT_SCHEMA_INVALID", "Ingredients must be a list."
        )
    for ingredient in ingredients:
        if not isinstance(ingredient, Mapping):
            raise ScanIngredientsError(
                "OUTPUT_SCHEMA_INVALID", "Ingredient entry must be an object."
            )
        if set(ingredient) - {"name", "confidence"}:
            raise ScanIngredientsError(
                "OUTPUT_SCHEMA_INVALID", "Ingredient contains unknown fields."
            )
        name = ingredient.get("name")
        confidence = ingredient.get("confidence")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ScanIngredientsError(
                "OUTPUT_SCHEMA_INVALID", "Ingredient name is invalid."
            )
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ScanIngredientsError(
                "OUTPUT_SCHEMA_INVALID", "Ingredient confidence is invalid."
            )
    return {"ingredients": ingredients, "image_quality": quality}
