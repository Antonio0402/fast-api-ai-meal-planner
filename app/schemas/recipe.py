from pydantic import BaseModel, Field


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
