"""Every error the API answers with looks the same to the browser:

    {"error": {"code": "...", "message": "...", "problems": ["..."]}}

The screens read `error.message` and show `error.problems` under the fields, so an
answer in any other shape leaves a person with "Could not save ...", nothing more.
FastAPI's own 422 and an unhandled exception are not in that shape, so main.py
registers the handlers at the bottom of this file for them too.
"""

import logging
import secrets

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.keyring import KeyRingError

log = logging.getLogger(__name__)


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


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    """One or more problems with submitted data, listed for the form."""

    status_code = 422
    code = "validation_failed"

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _body(code: str, message: str, problems: list[str] | None = None) -> dict:
    error = {"code": code, "message": message}
    if problems:
        error["problems"] = problems
    return {"error": error}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content=_body(exc.code, exc.message, getattr(exc, "problems", None)))


def _field(location: tuple) -> str:
    """"body" -> "customer" -> "phone" reads as "customer phone"; an index reads as the
    line it is: ("body", "lines", 2, "qty") -> "lines 3 qty"."""
    parts = [str(p + 1) if isinstance(p, int) else str(p).replace("_", " ")
             for p in location if p not in ("body", "query", "path")]
    return " ".join(parts)


async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422: something the screen sent does not fit the API (too long, the
    wrong type, missing). Said per field, so the person can see what to change."""
    problems = []
    for e in exc.errors():
        where = _field(tuple(e.get("loc") or ()))
        problems.append(f"{where}: {e.get('msg', 'is not valid')}" if where
                        else e.get("msg", "The request is not valid"))
    return JSONResponse(status_code=422,
                        content=_body("invalid_request", "; ".join(problems) or
                                      "The request is not valid", problems))


async def not_configured_handler(_: Request, exc: KeyRingError) -> JSONResponse:
    """A key setting this server needs is missing or malformed (EVIDENCE_KEYS,
    AUDIT_SEAL_KEYS, CREDENTIAL_KEYS). The message says which and how to make one; it
    never contains key material. 503, because the request was fine and the server is
    not ready: a fresh install whose backend/.env was copied from .env.example can
    read everything and save nothing until the keys are generated."""
    log.error("A key setting is missing: %s", exc)
    return JSONResponse(status_code=503, content=_body("not_configured", str(exc)))


async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything we did not expect. The person gets a reference they can quote, and the
    same reference is in the server log next to the traceback."""
    ref = secrets.token_hex(4)
    log.exception("Unhandled error on %s %s (reference %s)", request.method,
                  request.url.path, ref)
    return JSONResponse(status_code=500, content=_body(
        "server_error", f"Something went wrong on the server. Reference {ref}: it is in "
        f"the server's log with the details."))
