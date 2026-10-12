from pydantic import BaseModel, ConfigDict, Field


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_portions: int = Field(ge=1, le=12)
