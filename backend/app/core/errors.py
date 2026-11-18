"""App-wide error responses. Every error body is {"detail": ...}, the shape
frontend/src/api.ts reads.

Services raise AppError subclasses rather than FastAPI's HTTPException, so they stay
free of HTTP code (and can be called from a Celery task later). Each subclass carries
the status code and message the client gets; app_error_handler sends them.
"""

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """A failure the API reports to the client as {"detail": detail}.

    Subclasses set `status_code` and a default `detail`. Pass a message to say more for
    one case: `raise NotFound("Course not found.")`.
    """

    status_code = 400
    detail = "The request could not be completed."

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFound(AppError):
    status_code = 404
    detail = "Not found."


class PermissionDenied(AppError):
    status_code = 403
    detail = "You don't have permission to do this."


class Conflict(AppError):
    """The request clashes with data that already exists, e.g. a slug that's taken."""

    status_code = 409
    detail = "This conflicts with existing data."


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Sends any AppError as {"detail": ...}. Registered in main.py."""
    assert isinstance(exc, AppError)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's default 422 response, minus the rejected values.

    The default repeats each invalid value back as "input": for a too-short password,
    that is the password itself, in plain text, in a response that proxies and logging
    tools may record. "loc" and "msg" already say what's wrong.
    """
    assert isinstance(exc, RequestValidationError)
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
