from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    error = {"code": exc.code, "message": exc.message}
    if getattr(exc, "problems", None):
        error["problems"] = exc.problems
    return JSONResponse(status_code=exc.status_code, content={"error": error})
