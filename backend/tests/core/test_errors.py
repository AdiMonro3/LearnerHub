"""AppError and its handler: every module's domain errors reach the client as
{"detail": ...} with their own status code."""

import httpx
import pytest
from fastapi import FastAPI

from app.core.errors import (
    AppError,
    Conflict,
    NotFound,
    PermissionDenied,
    app_error_handler,
)
from app.main import app


class TeapotError(AppError):
    """A module-specific error, defined the way modules will define theirs."""

    status_code = 418
    detail = "I'm a teapot."


def _app_raising(error: AppError) -> FastAPI:
    test_app = FastAPI()
    test_app.add_exception_handler(AppError, app_error_handler)

    @test_app.get("/fail")
    async def fail() -> None:
        raise error

    return test_app


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (NotFound(), 404, "Not found."),
        (NotFound("Course not found."), 404, "Course not found."),
        (PermissionDenied(), 403, "You don't have permission to do this."),
        (Conflict("This slug is already taken."), 409, "This slug is already taken."),
        (TeapotError(), 418, "I'm a teapot."),
    ],
)
async def test_app_errors_become_detail_responses(
    error: AppError, status: int, detail: str
) -> None:
    transport = httpx.ASGITransport(app=_app_raising(error))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/fail")

    assert response.status_code == status
    assert response.json() == {"detail": detail}


def test_custom_detail_leaves_the_default_alone() -> None:
    assert NotFound("Course not found.").detail == "Course not found."
    assert NotFound().detail == "Not found."


def test_the_real_app_uses_the_handler() -> None:
    # The auth tests check this end to end too (e.g. 409 for a taken email), since
    # AuthError is now an AppError.
    assert app.exception_handlers[AppError] is app_error_handler
