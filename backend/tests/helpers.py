"""Helpers shared by the API tests of every module."""

import itertools
from dataclasses import dataclass

import httpx
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from tests.auth.helpers import bearer, register

_counter = itertools.count(1)


@dataclass(frozen=True)
class Email:
    """An email the app sent, as caught by the `outbox` fixture."""

    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class SignedIn:
    id: int
    full_name: str
    headers: dict[str, str]  # {"Authorization": "Bearer <access token>"}


async def sign_up(
    client: httpx.AsyncClient, db: AsyncSession, *, verified: bool = True
) -> SignedIn:
    """Register a new user through the API; return their id and auth headers.

    verified=True marks the email as confirmed, as if they had clicked the link.
    """
    n = next(_counter)
    full_name = f"Person {n}"
    response = await register(
        client, email=f"person{n}@example.com", full_name=full_name
    )
    assert response.status_code == 201, response.text
    body = response.json()
    if verified:
        await db.execute(
            update(User)
            .where(User.id == body["user"]["id"])
            .values(email_verified_at=func.now())
        )
    return SignedIn(
        id=body["user"]["id"],
        full_name=full_name,
        headers=bearer(body["access_token"]),
    )


@dataclass(frozen=True)
class Teacher(SignedIn):
    channel_slug: str


async def make_teacher(client: httpx.AsyncClient, db: AsyncSession) -> Teacher:
    """A verified user who owns a channel, and so is a TEACHER."""
    user = await sign_up(client, db)
    response = await client.post(
        "/channels", json={"name": f"Channel of {user.full_name}"}, headers=user.headers
    )
    assert response.status_code == 201, response.text
    return Teacher(
        id=user.id,
        full_name=user.full_name,
        headers=user.headers,
        channel_slug=response.json()["slug"],
    )
