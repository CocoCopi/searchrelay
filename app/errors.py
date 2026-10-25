from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    """An error that can be safely returned to an API client."""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str,
        *,
        param: str | None = None,
        error_type: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.param = param
        self.error_type = error_type


class BodyTooLarge(APIError):
    def __init__(self, message: str = "Request body is too large") -> None:
        super().__init__(413, message, "body_too_large")


def error_payload(
    message: str,
    code: str,
    *,
    param: str | None = None,
    error_type: str = "invalid_request_error",
) -> dict[str, dict[str, Any]]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                exc.message,
                exc.code,
                param=exc.param,
                error_type=exc.error_type,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        message = first.get("msg", "Invalid request")
        return JSONResponse(
            status_code=400,
            content=error_payload(
                f"Invalid request: {message}",
                "invalid_request",
                param=None,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(str(exc.detail), code),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "An internal error occurred",
                "internal_error",
            ),
        )
