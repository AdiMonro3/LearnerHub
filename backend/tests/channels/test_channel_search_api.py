"""GET /channels: browsing channels and finding them by name, page by page."""

from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from tests.helpers import SignedIn, sign_up


async def _channel(client: httpx.AsyncClient, db: AsyncSession, name: str) -> SignedIn:
    teacher = await sign_up(client, db)
    response = await client.post(
        "/channels", json={"name": name}, headers=teacher.headers
    )
    assert response.status_code == 201, response.text
    return teacher


async def _find(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/channels", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _names(client: httpx.AsyncClient, **params: Any) -> list[str]:
    return [channel["name"] for channel in (await _find(client, **params))["items"]]


async def test_browsing_channels_newest_first(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    for name in ("A", "B", "C"):
        await _channel(client, db, name)

    page = await _find(client)

    assert [channel["name"] for channel in page["items"]] == ["C", "B", "A"]
    assert page["next_cursor"] is None
    assert "email" not in page["items"][0]["teacher"]


async def test_finding_channels_by_name_ignores_case(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    for name in ("Python With Adi", "Learn PYTHON", "Java Corner"):
        await _channel(client, db, name)

    assert await _names(client, query="python") == ["Learn PYTHON", "Python With Adi"]
    assert await _names(client, query="  corner ") == ["Java Corner"]


async def test_percent_and_underscore_are_plain_characters(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    for name in ("100% Python", "Python", "snake_case club", "snakecase club"):
        await _channel(client, db, name)

    # As LIKE wildcards, "%" would match every channel and "_" any character.
    assert await _names(client, query="%") == ["100% Python"]
    assert await _names(client, query="e_c") == ["snake_case club"]


async def test_disabled_teachers_channels_are_not_listed(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await _channel(client, db, "Gone")
    await _channel(client, db, "Still Here")
    await db.execute(update(User).where(User.id == teacher.id).values(is_active=False))

    assert await _names(client) == ["Still Here"]


async def test_channels_page_by_page(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    # All five are created in one test transaction, so they share the same created_at:
    # only the id tells them apart, which is exactly the case the id is there for.
    for n in range(1, 6):
        await _channel(client, db, f"Channel {n}")

    pages, cursor = [], None
    while True:
        page = await _find(client, limit=2, **({"cursor": cursor} if cursor else {}))
        pages.append([channel["name"] for channel in page["items"]])
        if (cursor := page["next_cursor"]) is None:
            break

    assert pages == [
        ["Channel 5", "Channel 4"],
        ["Channel 3", "Channel 2"],
        ["Channel 1"],
    ]


async def test_a_bad_cursor_is_400(client: httpx.AsyncClient) -> None:
    response = await client.get("/channels", params={"cursor": "garbage"})

    assert response.status_code == 400
