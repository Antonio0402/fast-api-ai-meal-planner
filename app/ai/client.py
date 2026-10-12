from app.ai.vendor.gemini_adapter import GeminiAdapter
from app.ai.vendor.key_routing import ApiKeyEntry, ApiKeyRouter
from app.core.config import settings
from app.core.errors import APIError


def create_gemini_client() -> GeminiAdapter:
    if not settings.gemini_api_key:
        raise APIError(503, "AI_CONFIG_ERROR", "AI provider chưa được cấu hình.")
    key_router = ApiKeyRouter(
        [ApiKeyEntry(name="standard", api_key=settings.gemini_api_key)]
    )
    return GeminiAdapter(
        key_router=key_router,
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
