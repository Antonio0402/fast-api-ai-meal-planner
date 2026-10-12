from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InventoryItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    unit_code: str = Field(min_length=1, max_length=40)
    expiry_date: date | None = None

    @field_validator("name", "unit_code")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty.")
        return value


class InventoryBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[InventoryItemInput] = Field(min_length=1, max_length=100)


class InventoryItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    unit_code: str | None = Field(default=None, min_length=1, max_length=40)
    expiry_date: date | None = None

    @field_validator("name", "unit_code")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty.")
        return value
