from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.normalization import normalize_name
from app.services.unit_aliases import to_base

NonEmptyText = Annotated[str, Field(min_length=1)]


class RecipeItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=40)


class GeneratedRecipe(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    title: str = Field(min_length=1, max_length=200)
    minutes: int = Field(gt=0, le=240)
    servings: int = Field(ge=1, le=12)
    items: list[RecipeItem] = Field(min_length=1)
    steps: list[NonEmptyText] = Field(min_length=1)

    @model_validator(mode='after')
    def merge_ingredients(self) -> 'GeneratedRecipe':
        # Gộp cùng nguyên liệu sau quy đổi, không dùng lại tồn kho cho từng dòng.
        merged: dict[str, RecipeItem] = {}
        for item in self.items:
            name = normalize_name(item.name)
            previous = merged.get(name)
            if previous is None:
                merged[name] = item.model_copy()
                continue
            base = to_base(item.quantity, item.unit)
            previous_base = to_base(previous.quantity, previous.unit)
            if base.dimension != previous_base.dimension:
                raise ValueError('Một nguyên liệu có đơn vị không tương thích.')
            previous.quantity = (previous_base.quantity + base.quantity) / to_base(1, previous.unit).quantity
            # Kiểm tra lại cả tổng để không lưu số vô hạn do cộng dồn.
            merged[name] = RecipeItem.model_validate(previous.model_dump())
        self.items = list(merged.values())
        return self


class Recipe(GeneratedRecipe):
    id: str = Field(min_length=1)


class MissingItem(RecipeItem):
    check: bool | None = None


class RecipeMatch(BaseModel):
    recipe: Recipe
    servings: int = Field(ge=1, le=12)
    items: list[RecipeItem]
    missing: list[MissingItem]
    coverage: float = Field(ge=0, le=1)
