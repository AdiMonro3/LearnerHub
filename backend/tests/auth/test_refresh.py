"""Refresh-token rotation, reuse (theft) detection and the two-tabs grace window."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import RefreshToken
from app.modules.auth.security import hash_token
from app.modules.users.models import User
from tests.auth.helpers import as_device, bearer, refresh, refresh_cookie, register


async def _row(db: AsyncSession, raw: str) -> RefreshToken:
    token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    )
    assert token is not None
    return token


async def _make_rotation_old(db: AsyncSession, raw: str) -> None:
    """Pretend `raw` was rotated 11 seconds ago, past the grace window."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw))
        .values(revoked_at=datetime.now(UTC) - timedelta(seconds=11))
    )


async def test_refresh_rotates_the_token(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    first = refresh_cookie(await register(client))

    response = await refresh(client, first)

    assert response.status_code == 200
    second = refresh_cookie(response)
    assert second != first
    me = await client.get("/users/me", headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200
    # The database stores hashes only, and records the chain of rotations.
    old, new = await _row(db, first), await _row(db, second)
    assert old.revoked_at is not None and old.replaced_by_id == new.id
    assert new.revoked_at is None and new.family_id == old.family_id


async def test_reused_token_ends_the_whole_login(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    stolen_copy = refresh_cookie(await register(client))
    owners_latest = refresh_cookie(await refresh(client, stolen_copy))
    await _make_rotation_old(db, stolen_copy)

    response = await refresh(client, stolen_copy)

    assert response.status_code == 401
    assert "Max-Age=0" in response.headers["set-cookie"]  # dead cookie deleted
    # We can't tell the thief from the owner, so both are signed out.
    assert (await refresh(client, owners_latest)).status_code == 401


async def test_two_tabs_refreshing_at_once_both_stay_signed_in(
    client: httpx.AsyncClient,
) -> None:
    shared_cookie = refresh_cookie(await register(client))

    tab_a = await refresh(client, shared_cookie)
    tab_b = await refresh(client, shared_cookie)  # same cookie, a moment later

    assert tab_a.status_code == tab_b.status_code == 200
    assert (await refresh(client, refresh_cookie(tab_a))).status_code == 200
    assert (await refresh(client, refresh_cookie(tab_b))).status_code == 200


async def test_grace_window_cannot_undo_a_logout(client: httpx.AsyncClient) -> None:
    first = refresh_cookie(await register(client))
    second = refresh_cookie(await refresh(client, first))
    await client.post("/auth/logout", headers=as_device(second))

    response = await refresh(client, first)  # within 10 seconds of its rotation

    assert response.status_code == 401


async def test_expired_refresh_token_is_refused(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    raw = refresh_cookie(await register(client))
    await db.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    response = await refresh(client, raw)

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Your session has ended. Please sign in again."
    }


async def test_refresh_needs_a_real_cookie(client: httpx.AsyncClient) -> None:
    assert (await client.post("/auth/refresh")).status_code == 401
    assert (await refresh(client, "made-up-token")).status_code == 401


async def test_disabled_user_cannot_refresh(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    raw = refresh_cookie(await register(client))
    await db.execute(update(User).values(is_active=False))

    assert (await refresh(client, raw)).status_code == 401


async def test_browser_style_cookie_handling(client: httpx.AsyncClient) -> None:
    """Without handing cookies around: the client's jar keeps the latest cookie and
    sends it to /auth only, like a browser."""
    await register(client)

    for _ in range(3):
        assert (await client.post("/auth/refresh")).status_code == 200
    assert (await client.post("/auth/logout")).status_code == 204
    assert (await client.post("/auth/refresh")).status_code == 401
