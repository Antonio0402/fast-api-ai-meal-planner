"""Điểm gọi logic Gemini dùng cấu hình chung của FastAPI."""

from typing import Any, Mapping, Sequence

from app.ai.client import create_gemini_client
from app.ai.vendor.agent import (
    AgentFinalResponse, ChatMessage, ToolDispatcher, run_agent as _run_agent,
)
from app.ai.vendor.gemini_adapter import GeminiApiError, GeminiTransportError
from app.ai.vendor.recipe import GenerateRecipeError, generate_recipe as _generate_recipe
from app.core.errors import APIError


def _provider_error(exc: GeminiApiError | GeminiTransportError) -> APIError:
    if isinstance(exc, GeminiApiError):
        if exc.status_code == 429:
            return APIError(503, "AI_RATE_LIMITED", "Dịch vụ AI đang quá tải.")
        if exc.status_code in {401, 403}:
            return APIError(503, "AI_CONFIG_ERROR", "Cấu hình dịch vụ AI chưa hợp lệ.")
    return APIError(503, "AI_PROVIDER_UNAVAILABLE", "Dịch vụ AI tạm thời không khả dụng.")


def run_agent(
    *, user_message: str, history: Sequence[ChatMessage],
    tool_dispatcher: ToolDispatcher,
    auth_context: Mapping[str, Any] | None = None, max_tool_calls: int = 5,
) -> AgentFinalResponse:
    """Chạy agent; dispatcher backend chịu trách nhiệm xác thực và ghi dữ liệu."""
    try:
        return _run_agent(
            user_message=user_message, history=history,
            model_client=create_gemini_client(), tool_dispatcher=tool_dispatcher,
            auth_context=auth_context, max_tool_calls=max_tool_calls,
        )
    except (GeminiApiError, GeminiTransportError) as exc:
        raise _provider_error(exc) from exc


def generate_recipe(*, recipe: str, user_context: Mapping[str, Any]) -> str:
    """Diễn đạt lại công thức nguồn; khác luồng sinh recipe JSON từ inventory."""
    try:
        return _generate_recipe(
            recipe=recipe, user_context=user_context, model_client=create_gemini_client(),
        )
    except GenerateRecipeError as exc:
        status = 503 if exc.code == "EMPTY_MODEL_REPLY" else 422
        raise APIError(status, exc.code, "Không thể diễn đạt công thức với dữ liệu đã gửi.") from exc
    except (GeminiApiError, GeminiTransportError) as exc:
        raise _provider_error(exc) from exc
