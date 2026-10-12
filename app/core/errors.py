from dataclasses import dataclass
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


@dataclass
class APIError(Exception):
    status_code: int
    code: str
    message: str


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message},
            "detail": {"code": code, "message": message}
        },
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
