from dataclasses import dataclass
from math import ceil


UNIT_ALIASES = {
    "gram": "g",
    "gr": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "lít": "l",
    "lit": "l",
    "liter": "l",
    "litre": "l",
    "milliliter": "ml",
    "millilitre": "ml",
    "trái": "quả",
    "trai": "quả",
    "qua": "quả",
    "muỗng": "thìa",
    "muong": "thìa",
    "thia": "thìa",
    "muỗng canh": "thìa",
    "thìa canh": "thìa",
}


@dataclass(frozen=True)
class BaseQuantity:
    dimension: str
    quantity: float


def normalize_unit(unit: str) -> str:
    normalized = " ".join(unit.strip().lower().split())
    return UNIT_ALIASES.get(normalized, normalized)


def to_base(quantity: float, unit: str) -> BaseQuantity:
    normalized = normalize_unit(unit)
    if normalized == "g":
        return BaseQuantity("g", quantity)
    if normalized == "kg":
        return BaseQuantity("g", quantity * 1000)
    if normalized == "ml":
        return BaseQuantity("ml", quantity)
    if normalized == "l":
        return BaseQuantity("ml", quantity * 1000)
    return BaseQuantity(normalized or "?", quantity)


def from_base(quantity: float, dimension: str) -> tuple[float, str]:
    rounded_up = max(1 if quantity > 0 else 0, ceil(quantity - 1e-9))
    if dimension == "g" and rounded_up >= 1000:
        return rounded_up / 1000, "kg"
    if dimension == "ml" and rounded_up >= 1000:
        return rounded_up / 1000, "l"
    return rounded_up, dimension
