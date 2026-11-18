"""Email verification and password reset links."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import EmailToken
from tests.auth.helpers import (
    bearer,
    link_token,
    login,
    refresh,
    refresh_cookie,
    register,
)
from tests.helpers import Email

INVALID_LINK = {"detail": "This link is invalid or has expired."}
NEW_PASSWORD = "a-brand-new-password"


async def _request_verification(client: httpx.AsyncClient, access_token: str) -> int:
    response = await client.post(
        "/auth/verify-email/request", headers=bearer(access_token)
    )
    return response.status_code


async def _confirm_verification(
    client: httpx.AsyncClient, token: str
) -> httpx.Response:
    return await client.post("/auth/verify-email/confirm", json={"token": token})


# --- Verify email ------------------------------------------------------------------------


async def test_verify_email(client: httpx.AsyncClient, outbox: list[Email]) -> None:
    access_token = (await register(client)).json()["access_token"]

    assert await _request_verification(client, access_token) == 204

    [email] = outbox
    assert email.to == "ada@example.com"
    assert "http://localhost:5173/verify-email?token=" in email.body
    token = link_token(email.body)
    assert (await _confirm_verification(client, token)).status_code == 204
    me = await client.get("/users/me", headers=bearer(access_token))
    assert me.json()["email_verified"] is True
    # Each link works once.
    reused = await _confirm_verification(client, token)
    assert reused.status_code == 400 and reused.json() == INVALID_LINK


async def test_verified_email_gets_no_more_links(
    client: httpx.AsyncClient, outbox: list[Email]
) -> None:
    access_token = (await register(client)).json()["access_token"]
    await _request_verification(client, access_token)
    await _confirm_verification(client, link_token(outbox[0].body))
    outbox.clear()

    assert await _request_verification(client, access_token) == 204
    assert outbox == []


async def test_only_the_newest_link_works(
    client: httpx.AsyncClient, outbox: list[Email]
) -> None:
    access_token = (await register(client)).json()["access_token"]
    await _request_verification(client, access_token)
    await _request_verification(client, access_token)
    older, newer = (link_token(email.body) for email in outbox)

    assert (await _confirm_verification(client, older)).status_code == 400
    assert (await _confirm_verification(client, newer)).status_code == 204


async def test_expired_link_is_refused(
    client: httpx.AsyncClient, db: AsyncSession, outbox: list[Email]
) -> None:
    access_token = (await register(client)).json()["access_token"]
    await _request_verification(client, access_token)
    await db.execute(
        update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    response = await _confirm_verification(client, link_token(outbox[0].body))

    assert response.status_code == 400 and response.json() == INVALID_LINK


# --- Password reset ----------------------------------------------------------------------


async def test_password_reset(client: httpx.AsyncClient, outbox: list[Email]) -> None:
    old_session = refresh_cookie(await register(client))

    response = await client.post(
        "/auth/password-reset/request", json={"email": "ADA@example.com"}
    )

    assert response.status_code == 204
    [email] = outbox
    assert "http://localhost:5173/reset-password?token=" in email.body
    confirmed = await client.post(
        "/auth/password-reset/confirm",
        json={"token": link_token(email.body), "new_password": NEW_PASSWORD},
    )
    assert confirmed.status_code == 204
    assert (await login(client)).status_code == 401  # old password
    signed_in = await login(client, password=NEW_PASSWORD)
    assert signed_in.status_code == 200
    # The link reached the inbox, which proves the address is theirs.
    assert signed_in.json()["user"]["email_verified"] is True
    # Whoever knew the old password is signed out.
    assert (await refresh(client, old_session)).status_code == 401


async def test_reset_for_an_unknown_email_looks_the_same(
    client: httpx.AsyncClient, outbox: list[Email]
) -> None:
    response = await client.post(
        "/auth/password-reset/request", json={"email": "nobody@example.com"}
    )

    assert response.status_code == 204
    assert outbox == []


async def test_a_link_only_works_for_its_own_purpose(
    client: httpx.AsyncClient, outbox: list[Email]
) -> None:
    access_token = (await register(client)).json()["access_token"]
    await _request_verification(client, access_token)

    response = await client.post(
        "/auth/password-reset/confirm",
        json={"token": link_token(outbox[0].body), "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400 and response.json() == INVALID_LINK
