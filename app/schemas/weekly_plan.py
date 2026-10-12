from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: dict[str, str] = Field(default_factory=dict)

    @field_validator("slots")
    @classmethod
    def validate_slot_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for key, recipe_id in value.items():
            parts = key.split("-")
            if (
                len(parts) != 2
                or not parts[0].isdigit()
                or not parts[1].isdigit()
                or not 0 <= int(parts[0]) <= 6
                or not 0 <= int(parts[1]) <= 2
                or not recipe_id.strip()
            ):
                raise ValueError("Invalid meal-plan slot.")
        return value


class DraftPlanInput(PlanSlots):
    portions: int = Field(ge=1, le=12)


class PublishPlanInput(DraftPlanInput):
    expected_version: int | None


class GeneratePlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    portions: int = Field(ge=1, le=12)


class ShoppingCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredient_id: UUID
    dimension_key: str = Field(min_length=1, max_length=40)
    is_checked: bool


class ShoppingChecksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID
    items: list[ShoppingCheckItem] = Field(min_length=1, max_length=200)
