"""One error model for the whole API.

v1 (and /health, /ready): {"error": {"code", "message", "request_id", "details"}} with UPPER_SNAKE codes.
Legacy /api/*: {"request_id", "error": {"code", "message", "details"}} with the original lowercase
codes, kept so existing clients don't break. Stack traces never leave the server.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.errors import LEGACY_CODES, AppError, ErrorCode

_STATUS_CODES = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    429: ErrorCode.RATE_LIMITED,
}


def is_legacy_path(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith("/api/v1/")


def error_response(
    request: Request, status: int, code: ErrorCode, message: str, details: list[dict] | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    if is_legacy_path(request.url.path):
        body = {"request_id": request_id, "error": {"code": LEGACY_CODES.get(code, code.value.lower()), "message": message, "details": details}}
    else:
        body = {"error": {"code": code.value, "message": message, "request_id": request_id, "details": details}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error_response(request, exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]} for err in exc.errors()]
        return error_response(request, 422, ErrorCode.VALIDATION_ERROR, "Some of the request fields are invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, ErrorCode.VALIDATION_ERROR if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR)
        return error_response(request, exc.status_code, code, str(exc.detail))
