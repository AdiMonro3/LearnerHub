"""Email and password: register, login, the Swagger token, logout, changing passwords."""

from typing import Any
from unittest.mock import ANY

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.security import create_access_token, needs_rehash
from app.modules.users.models import User
from tests.auth.helpers import (
    PASSWORD,
    as_device,
    bearer,
    login,
    refresh,
    refresh_cookie,
    register,
)
from tests.factories import make_user

NEW_PASSWORD = "a-brand-new-password"


async def test_providers_offer_password_only_until_google_is_set_up(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/auth/providers")

    assert response.json() == {"password": True, "google": False}


# --- Register ----------------------------------------------------------------------------


async def test_register_signs_the_user_in(client: httpx.AsyncClient) -> None:
    response = await register(client, full_name="  Ada Lovelace  ")

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "access_token": ANY,
        "token_type": "bearer",
        "expires_in": 15 * 60,
        "user": {
            "id": ANY,
            "email": "ada@example.com",
            "full_name": "Ada Lovelace",
            "avatar_url": None,
            "email_verified": False,
            "has_password": True,
            "google_linked": False,
            "created_at": ANY,
        },
    }
    me = await client.get("/users/me", headers=bearer(body["access_token"]))
    assert me.json() == body["user"]


async def test_refresh_cookie_is_locked_down(client: httpx.AsyncClient) -> None:
    cookie = (await register(client)).headers["set-cookie"]

    assert cookie.startswith("refresh_token=")
    for attribute in ("HttpOnly", "Path=/auth", "SameSite=lax", "Max-Age=604800"):
        assert attribute in cookie


async def test_blank_full_name_is_stored_as_none(client: httpx.AsyncClient) -> None:
    response = await register(client, full_name="   ")

    assert response.json()["user"]["full_name"] is None


async def test_email_is_unique_ignoring_case(client: httpx.AsyncClient) -> None:
    await register(client, email="ada@example.com")

    response = await register(client, email="ADA@Example.COM")

    assert response.status_code == 409
    assert response.json() == {"detail": "An account with this email already exists."}


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        (
            {"email": "ada@example.com", "password": "Hunter2"},  # 7 characters
            "password",
            "Password must be at least 8 characters.",
        ),
        (
            {"email": "ada@example.com", "password": "x" * 129},
            "password",
            "Password must be at most 128 characters.",
        ),
        ({"email": "not-an-email", "password": PASSWORD}, "email", ANY),
    ],
    ids=["password-too-short", "password-too-long", "bad-email"],
)
async def test_register_validates_input(
    client: httpx.AsyncClient, payload: dict[str, Any], field: str, message: str
) -> None:
    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", field]
    assert error["msg"] == message
    # The 422 body leaves out the rejected values, so it never repeats a password.
    assert payload["password"] not in response.text


# --- Login -------------------------------------------------------------------------------


async def test_login_ignores_email_case(client: httpx.AsyncClient) -> None:
    await register(client, email="ada@example.com")

    response = await login(client, email="ADA@EXAMPLE.COM")

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "ada@example.com"


async def test_wrong_password_and_unknown_email_look_the_same(
    client: httpx.AsyncClient,
) -> None:
    await register(client)

    wrong_password = await login(client, password="not-the-password")
    unknown_email = await login(client, email="nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json() == {"detail": "Incorrect email or password."}


async def test_google_only_account_has_no_password_to_log_in_with(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await make_user(db, email="ada@example.com")  # password_hash is NULL

    response = await login(client)

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect email or password."}


async def test_disabled_account_cannot_log_in(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await register(client)
    await db.execute(update(User).values(is_active=False))

    response = await login(client)

    assert response.status_code == 403
    assert response.json() == {"detail": "This account has been disabled."}


async def test_outdated_password_hash_is_upgraded_at_login(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    old_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024).hash(PASSWORD)
    user = await make_user(db, email="ada@example.com")
    user.password_hash = old_hash
    await db.flush()

    assert (await login(client)).status_code == 200

    await db.refresh(user)
    assert user.password_hash != old_hash
    assert user.password_hash is not None
    assert not needs_rehash(user.password_hash)


async def test_token_endpoint_serves_swagger_authorize(
    client: httpx.AsyncClient,
) -> None:
    await register(client)

    response = await client.post(
        "/auth/token", data={"username": "ada@example.com", "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json() == {"access_token": ANY, "token_type": "bearer"}
    assert "set-cookie" not in response.headers  # no session for Swagger
    me = await client.get("/users/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200


# --- Logout ------------------------------------------------------------------------------


async def test_logout_ends_only_this_devices_session(client: httpx.AsyncClient) -> None:
    laptop = refresh_cookie(await register(client))
    phone = refresh_cookie(await login(client))

    response = await client.post("/auth/logout", headers=as_device(laptop))

    assert response.status_code == 204
    cleared = response.headers["set-cookie"]
    assert cleared.startswith("refresh_token=") and "Max-Age=0" in cleared
    assert "Path=/auth" in cleared  # same path as when set, or the browser keeps it
    assert (await refresh(client, laptop)).status_code == 401
    assert (await refresh(client, phone)).status_code == 200


async def test_logout_when_already_signed_out_still_succeeds(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.post("/auth/logout")).status_code == 204


# --- Change password ---------------------------------------------------------------------


async def test_change_password_signs_out_the_other_devices(
    client: httpx.AsyncClient,
) -> None:
    laptop = await register(client)
    phone = refresh_cookie(await login(client))

    response = await client.post(
        "/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={
            **bearer(laptop.json()["access_token"]),
            **as_device(refresh_cookie(laptop)),
        },
    )

    assert response.status_code == 204
    assert (await refresh(client, refresh_cookie(laptop))).status_code == 200
    assert (await refresh(client, phone)).status_code == 401
    assert (await login(client)).status_code == 401
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200


@pytest.mark.parametrize("current_password", [None, "not-the-password"])
async def test_change_password_needs_the_current_one(
    client: httpx.AsyncClient, current_password: str | None
) -> None:
    access_token = (await register(client)).json()["access_token"]
    body = {"new_password": NEW_PASSWORD}
    if current_password is not None:
        body["current_password"] = current_password

    response = await client.post(
        "/auth/password", json=body, headers=bearer(access_token)
    )

    # 400, not 401: a 401 would make the frontend refresh the session and retry.
    assert response.status_code == 400
    assert response.json() == {"detail": "Current password is incorrect."}


async def test_google_only_account_can_set_a_first_password(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    user = await make_user(db, email="ada@example.com")

    response = await client.post(
        "/auth/password",
        json={"new_password": PASSWORD},
        headers=bearer(create_access_token(user.id, [])),
    )

    assert response.status_code == 204
    assert (await login(client)).json()["user"]["has_password"] is True


# --- CORS --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [("http://localhost:5173", True), ("https://evil.example", False)],
)
async def test_only_the_frontend_may_call_with_cookies(
    client: httpx.AsyncClient, origin: str, allowed: bool
) -> None:
    preflight = await client.options(
        "/auth/refresh",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )

    assert (preflight.headers.get("access-control-allow-origin") == origin) is allowed
    if allowed:
        assert preflight.headers["access-control-allow-credentials"] == "true"
