"""Chuyển lỗi Gemini thành mã API an toàn cho chức năng lập thực đơn."""

from app.ai.vendor.gemini_adapter import (
    GeminiApiError,
    GeminiTimeoutError,
    GeminiTransportError,
)
from app.core.errors import APIError


def meal_generation_error(error: Exception) -> APIError:
    if isinstance(error, GeminiTimeoutError) or (
        isinstance(error, GeminiApiError) and error.status_code == 504
    ):
        return APIError(504, "AI_TIMEOUT", "Lập thực đơn quá lâu. Vui lòng thử lại.")
    if isinstance(error, GeminiApiError):
        if error.status_code == 429:
            return APIError(429, "AI_RATE_LIMITED", "Dịch vụ lập thực đơn đang quá tải.")
        if error.status_code in {401, 403}:
            return APIError(503, "AI_CONFIG_ERROR", "Dịch vụ lập thực đơn chưa được cấu hình.")
    if isinstance(error, (GeminiApiError, GeminiTransportError)):
        return APIError(503, "AI_PROVIDER_UNAVAILABLE", "Dịch vụ lập thực đơn tạm thời không khả dụng.")
    return APIError(502, "AI_OUTPUT_INVALID", "AI chưa tạo được thực đơn hợp lệ.")
