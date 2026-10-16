"""Validated mobile snapshot payloads."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.allergy import AllergyList

SLOT_KEYS = {f"{day}-{meal}" for day in range(7) for meal in range(3)}


class Ingredient(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=40)
    quantity: float | None = Field(default=None, ge=0)
    expiry: str | None = None

    @field_validator("expiry")
    @classmethod
    def valid_expiry(cls, value: str | None) -> str | None:
        if value is not None:
            datetime.strptime(value, "%d/%m/%Y")
        return value


class SavedPlan(BaseModel):
    weekStart: date
    slots: dict[str, str]
    portions: int = Field(ge=1, le=12)
    version: int = Field(gt=0)


class AppData(BaseModel):
    inventory: list[Ingredient]
    portions: int = Field(ge=1, le=12)
    allergies: AllergyList
    weekStart: date
    slots: dict[str, str]
    savedPlan: SavedPlan | None = None
    checked: dict[str, bool]

    @field_validator("slots")
    @classmethod
    def valid_slots(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) - SLOT_KEYS or any(not recipe_id for recipe_id in value.values()):
            raise ValueError("Invalid slot map")
        return value

    @field_validator("inventory")
    @classmethod
    def unique_inventory(cls, value: list[Ingredient]) -> list[Ingredient]:
        if len({item.id for item in value}) != len(value):
            raise ValueError("Duplicate inventory ID")
        return value


class AppDataResponse(BaseModel):
    data: AppData
    revision: int = Field(ge=0)


class ShoppingItem(BaseModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    toBuy: float = Field(ge=0)
    group: str = Field(min_length=1)
    check: bool
