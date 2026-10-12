import json

import pytest

from app.ai.vendor.gemini_adapter import GeminiApiError, GeminiTimeoutError
from app.core.errors import APIError
from app.services import meal_plan_service


class Model:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error

    def generate_text(self, prompt):
        if self.error:
            raise self.error
        assert "breakfast" in prompt
        return self.output


def test_generate_ai_slots_accepts_only_owned_catalog_ids(monkeypatch):
    recipes = [{"id": "owned", "title": "Canh rau", "items": [{"name": "Rau"}]}]
    slots = {f"{day}-{meal}": "owned" for day in range(7) for meal in range(3)}
    monkeypatch.setattr(meal_plan_service, "create_gemini_client", lambda: Model(json.dumps({"slots": slots})))
    assert meal_plan_service.generate_ai_slots(recipes) == slots

    slots["0-0"] = "other-user"
    monkeypatch.setattr(meal_plan_service, "create_gemini_client", lambda: Model(json.dumps({"slots": slots})))
    with pytest.raises(APIError, match="AI chưa tạo") as error:
        meal_plan_service.generate_ai_slots(recipes)
    assert error.value.code == "AI_OUTPUT_INVALID"


@pytest.mark.parametrize("failure, code, status", [
    (GeminiTimeoutError("private"), "AI_TIMEOUT", 504),
    (GeminiApiError(429), "AI_RATE_LIMITED", 429),
])
def test_generate_ai_slots_preserves_provider_failure(monkeypatch, failure, code, status):
    monkeypatch.setattr(meal_plan_service, "create_gemini_client", lambda: Model(error=failure))
    with pytest.raises(APIError) as error:
        meal_plan_service.generate_ai_slots([{"id": "owned", "title": "Canh rau"}])
    assert (error.value.code, error.value.status_code) == (code, status)
