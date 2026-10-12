from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScanConfirmItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID | None = None
    ingredient_id: UUID | None = None
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


class ScanConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ScanConfirmItem] = Field(max_length=100)
