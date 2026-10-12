from pydantic import BaseModel, ConfigDict


class MealPlanBase(BaseModel):
    title: str
    description: str | None = None


class MealPlanCreate(MealPlanBase):
    user_id: int


class MealPlanRead(MealPlanBase):
    id: int
    user_id: int

    model_config = ConfigDict(from_attributes=True)
