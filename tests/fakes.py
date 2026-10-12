from typing import Any

from app.ai.vendor.gemini_adapter import GeminiApiError, GeminiTransportError


class FakeGeminiClient:
    def __init__(
        self,
        *,
        scan_result: Any = None,
        plan_result: str = '{"slots":{}}',
        failure: str | None = None,
    ) -> None:
        self.scan_result = scan_result or {
            "image_quality": "ok",
            "ingredients": [{"name": "Cà chua", "confidence": None}],
        }
        self.plan_result = plan_result
        self.failure = failure

    def scan_ingredients(self, **_: Any) -> Any:
        self._raise_if_configured()
        return self.scan_result

    def generate_text(self, _: str) -> str:
        self._raise_if_configured()
        return self.plan_result

    def _raise_if_configured(self) -> None:
        if self.failure == "rate_limited":
            raise GeminiApiError(429)
        if self.failure == "provider_unavailable":
            raise GeminiTransportError("fake provider failure")
