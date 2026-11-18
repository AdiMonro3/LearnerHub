"""The route guards in app/modules/auth/deps.py, through GET /users/me."""

from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import require_role
from app.modules.auth.security import create_access_token, encode_jwt
from app.modules.auth.service import get_user
from app.modules.users.models import Role, User, UserRole
from tests.auth.helpers import bearer, register


async def test_no_token_is_401_with_a_bearer_challenge(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/users/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        encode_jwt({"sub": "1"}, token_type="access", lifetime=timedelta(seconds=-1)),
        encode_jwt({"sub": "1"}, token_type="other", lifetime=timedelta(minutes=5)),
        create_access_token(999_999_999, []),  # valid signature, no such user
    ],
    ids=["garbage", "expired", "wrong-type", "unknown-user"],
)
async def test_bad_tokens_are_401(client: httpx.AsyncClient, token: str) -> None:
    # 401, never 403: the frontend answers 401 by refreshing and retrying.
    response = await client.get("/users/me", headers=bearer(token))

    assert response.status_code == 401


async def test_disabled_user_is_refused_even_with_a_valid_token(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    access_token = (await register(client)).json()["access_token"]
    await db.execute(update(User).values(is_active=False))

    response = await client.get("/users/me", headers=bearer(access_token))

    # Takes effect at once, without waiting 15 minutes for the token to expire.
    assert response.status_code == 403


async def test_require_role_reads_roles_from_the_database(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    user_id = (await register(client)).json()["user"]["id"]
    user = await get_user(db, user_id)
    assert user is not None
    teachers_only = require_role(Role.TEACHER, Role.ADMIN)

    with pytest.raises(HTTPException) as refused:
        await teachers_only(user)  # a new account is a LEARNER only
    assert refused.value.status_code == 403

    user.roles.append(UserRole(role=Role.TEACHER))
    await db.flush()
    assert await teachers_only(user) is user
