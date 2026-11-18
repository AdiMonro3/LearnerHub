"""Small helpers for the auth API tests."""

import re
from typing import Any

import httpx

EMAIL = "ada@example.com"
PASSWORD = "correct-horse-battery"


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def as_device(refresh_token: str) -> dict[str, str]:
    """Headers that send this refresh cookie instead of the one in the client's jar,
    to act as one particular device (or a thief holding a copy)."""
    return {"Cookie": f"refresh_token={refresh_token}"}


def refresh_cookie(response: httpx.Response) -> str:
    return response.cookies["refresh_token"]


async def register(
    client: httpx.AsyncClient,
    email: str = EMAIL,
    password: str = PASSWORD,
    **fields: Any,
) -> httpx.Response:
    return await client.post(
        "/auth/register", json={"email": email, "password": password, **fields}
    )


async def login(
    client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/auth/login", json={"email": email, "password": password})


async def refresh(client: httpx.AsyncClient, refresh_token: str) -> httpx.Response:
    return await client.post("/auth/refresh", headers=as_device(refresh_token))


def link_token(email_body: str) -> str:
    """The token from the link in an email."""
    match = re.search(r"\?token=([\w-]+)", email_body)
    assert match, email_body
    return match.group(1)
