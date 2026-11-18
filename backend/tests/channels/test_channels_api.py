"""The /channels API: creating, reading and editing a teacher's channel."""

from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels import service
from app.modules.users.models import Role, User, UserRole
from tests.helpers import SignedIn, sign_up


async def _roles(db: AsyncSession, user_id: int) -> set[Role]:
    return set(
        await db.scalars(select(UserRole.role).where(UserRole.user_id == user_id))
    )


async def _create(
    client: httpx.AsyncClient, who: SignedIn, **body: Any
) -> httpx.Response:
    return await client.post(
        "/channels", json={"name": "Python With Adi", **body}, headers=who.headers
    )


# --- Creating ----------------------------------------------------------------------------


async def test_creating_a_channel_makes_you_a_teacher(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)

    response = await _create(client, me, bio="Python, simply.")

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Python With Adi"
    assert body["slug"] == "python-with-adi"  # made from the name
    assert body["bio"] == "Python, simply."
    assert body["price_monthly_paise"] == 0  # free unless set
    # Public teacher details only: the email is never in a channel response.
    assert body["teacher"] == {
        "id": me.id,
        "full_name": me.full_name,
        "avatar_url": None,
    }
    assert await _roles(db, me.id) == {Role.LEARNER, Role.TEACHER}


async def test_you_can_choose_the_url(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)

    response = await _create(client, me, slug="  Adi-Codes ")

    assert response.json()["slug"] == "adi-codes"


async def test_a_taken_name_gets_a_numbered_url(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await _create(client, await sign_up(client, db), name="Python")

    response = await _create(client, await sign_up(client, db), name="Python")

    assert response.json()["slug"] == "python-2"


async def test_a_chosen_url_that_is_taken_is_409(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await _create(client, await sign_up(client, db), slug="adi")
    other = await sign_up(client, db)

    response = await _create(client, other, slug="adi")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "This channel URL is already taken. Choose another."
    }
    # Rolled back as a whole: no TEACHER role without a channel.
    assert await _roles(db, other.id) == {Role.LEARNER}


async def test_two_requests_racing_for_one_url_get_409_not_500(
    client: httpx.AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _create(client, await sign_up(client, db), name="Python")

    # As if this request had checked for free slugs just before the first one saved.
    async def stale_free_slug(*args: Any, **kwargs: Any) -> str:
        return "python"

    monkeypatch.setattr(service, "free_slug", stale_free_slug)
    response = await _create(client, await sign_up(client, db), name="Python")

    assert response.status_code == 409


async def test_one_channel_per_teacher(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)
    await _create(client, me)

    response = await _create(client, me, name="Second Channel")

    assert response.status_code == 409
    assert response.json() == {"detail": "You already have a channel."}


async def test_an_unverified_email_cannot_create_a_channel(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db, verified=False)

    response = await _create(client, me)

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Verify your email address before creating a channel."
    }
    assert await _roles(db, me.id) == {Role.LEARNER}


async def test_creating_a_channel_needs_sign_in(client: httpx.AsyncClient) -> None:
    response = await client.post("/channels", json={"name": "Python With Adi"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"slug": "me"},  # reserved: /channels/me
        {"slug": "python with adi"},
        {"slug": "python--adi"},
        {"slug": "-python"},
        {"slug": "x" * 101},
        {"price_monthly_paise": -1},
        {"price_monthly_paise": 10_000_001},
        {"price_monthly_paise": 499.5},
    ],
    ids=lambda body: next(iter(body)) + "=" + str(next(iter(body.values())))[:12],
)
async def test_invalid_details_are_422(
    client: httpx.AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    response = await _create(client, await sign_up(client, db), **body)

    assert response.status_code == 422


# --- Reading -----------------------------------------------------------------------------


async def test_my_channel(client: httpx.AsyncClient, db: AsyncSession) -> None:
    me = await sign_up(client, db)
    created = (await _create(client, me)).json()

    response = await client.get("/channels/me", headers=me.headers)

    assert response.status_code == 200
    assert response.json() == created


async def test_my_channel_when_you_have_none_is_404(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)

    response = await client.get("/channels/me", headers=me.headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "You don't have a channel yet."}


async def test_the_public_page_needs_no_sign_in(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    created = (await _create(client, await sign_up(client, db))).json()

    response = await client.get("/channels/python-with-adi")

    assert response.status_code == 200
    assert response.json() == created


async def test_an_unknown_channel_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/channels/nobody-here")

    assert response.status_code == 404
    assert response.json() == {"detail": "Channel not found."}


async def test_a_disabled_teachers_channel_is_hidden(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)
    await _create(client, me)
    await db.execute(update(User).where(User.id == me.id).values(is_active=False))

    response = await client.get("/channels/python-with-adi")

    assert response.status_code == 404


# --- Editing -----------------------------------------------------------------------------


async def test_only_the_fields_sent_are_changed(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)
    await _create(client, me, bio="Old bio")

    async def patch(body: dict[str, Any]) -> dict[str, Any]:
        response = await client.patch("/channels/me", json=body, headers=me.headers)
        assert response.status_code == 200, response.text
        return dict(response.json())

    changed = await patch({"bio": "New bio"})
    assert (changed["name"], changed["bio"]) == ("Python With Adi", "New bio")

    assert (await patch({"price_monthly_paise": 49_900}))[
        "price_monthly_paise"
    ] == 49_900
    assert (await patch({"bio": None}))["bio"] is None  # null clears the bio
    assert (await patch({}))["price_monthly_paise"] == 49_900  # nothing sent: no change


async def test_renaming_keeps_the_url(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)
    await _create(client, me)

    response = await client.patch(
        "/channels/me", json={"name": "Adi Teaches Python"}, headers=me.headers
    )

    assert response.json()["slug"] == "python-with-adi"


async def test_changing_the_url(client: httpx.AsyncClient, db: AsyncSession) -> None:
    me = await sign_up(client, db)
    await _create(client, me)

    response = await client.patch(
        "/channels/me", json={"slug": "adi-codes"}, headers=me.headers
    )

    assert response.json()["slug"] == "adi-codes"
    assert (await client.get("/channels/adi-codes")).status_code == 200
    assert (await client.get("/channels/python-with-adi")).status_code == 404


async def test_changing_to_a_taken_url_is_409(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await _create(client, await sign_up(client, db), slug="taken")
    me = await sign_up(client, db)
    await _create(client, me)

    response = await client.patch(
        "/channels/me", json={"slug": "taken"}, headers=me.headers
    )

    assert response.status_code == 409
    assert (await client.get("/channels/python-with-adi")).status_code == 200


@pytest.mark.parametrize("field", ["name", "slug", "price_monthly_paise"])
async def test_required_fields_cannot_be_set_to_null(
    client: httpx.AsyncClient, db: AsyncSession, field: str
) -> None:
    me = await sign_up(client, db)
    await _create(client, me)

    response = await client.patch(
        "/channels/me", json={field: None}, headers=me.headers
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


async def test_editing_without_a_channel_is_404(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    me = await sign_up(client, db)

    response = await client.patch(
        "/channels/me", json={"bio": "Hi"}, headers=me.headers
    )

    assert response.status_code == 404
