from dataclasses import dataclass
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.error import ErrorDetail, ErrorResponse

# Các lỗi chung của route có xác thực, kể cả lỗi validation do FastAPI tạo.
AUTH_ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Thiếu hoặc không xác minh được phiên Supabase."},
    409: {"model": ErrorResponse, "description": "Dữ liệu xung đột với trạng thái hiện tại."},
    422: {"model": ErrorResponse, "description": "Dữ liệu yêu cầu hoặc điều kiện nghiệp vụ không hợp lệ."},
    500: {"model": ErrorResponse, "description": "Lỗi nội bộ; chi tiết riêng tư không được trả về."},
}

AI_ERROR_RESPONSES = {
    429: {"model": ErrorResponse, "description": "AI_RATE_LIMITED: dịch vụ AI quá tải."},
    502: {"model": ErrorResponse, "description": "AI_OUTPUT_INVALID: kết quả AI không hợp lệ."},
    503: {"model": ErrorResponse, "description": "AI_CONFIG_ERROR hoặc AI_PROVIDER_UNAVAILABLE."},
    504: {"model": ErrorResponse, "description": "AI_TIMEOUT: xử lý AI quá thời gian."},
}

logger = logging.getLogger(__name__)


@dataclass
class APIError(Exception):
    status_code: int
    code: str
    message: str


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    detail = ErrorDetail(code=code, message=message)
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=detail, detail=detail).model_dump(),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(404, "NOT_FOUND", "Không tìm thấy tài nguyên.")
        if exc.status_code == 405:
            return error_response(405, "METHOD_NOT_ALLOWED", "Phương thức không được hỗ trợ.")
        return error_response(exc.status_code, "HTTP_ERROR", "Yêu cầu không hợp lệ.")

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(422, "VALIDATION_ERROR", "Dữ liệu yêu cầu không hợp lệ.")

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(
        _: Request, _exc: IntegrityError
    ) -> JSONResponse:
        return error_response(
            409,
            "CONFLICT",
            "Dữ liệu xung đột với trạng thái hiện tại.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, _exc: Exception) -> JSONResponse:
        logger.error("Unhandled request failure.")
        return error_response(500, "INTERNAL_ERROR", "Đã xảy ra lỗi nội bộ.")
