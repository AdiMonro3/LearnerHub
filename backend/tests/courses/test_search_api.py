"""GET /courses: browsing published courses, full-text search, and paging through."""

from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from tests.courses.helpers import create_course, set_status
from tests.helpers import SignedIn, make_teacher


async def _publish(
    client: httpx.AsyncClient, teacher: SignedIn, title: str, **body: Any
) -> dict[str, Any]:
    course = await create_course(client, teacher, title=title, **body)
    return await set_status(client, teacher, course, "PUBLISHED")


async def _find(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/courses", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _titles(client: httpx.AsyncClient, **params: Any) -> list[str]:
    return [course["title"] for course in (await _find(client, **params))["items"]]


async def _every_page(
    client: httpx.AsyncClient, **params: Any
) -> list[list[dict[str, Any]]]:
    """Follow next_cursor until the last page; returns the items of every page."""
    pages, cursor = [], None
    while True:
        page = await _find(client, **params, **({"cursor": cursor} if cursor else {}))
        pages.append(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return pages


# --- Browsing ----------------------------------------------------------------------------


async def test_browsing_lists_published_courses_newest_first(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    await _publish(client, teacher, "Older")
    await _publish(client, teacher, "Newer")
    await create_course(client, teacher, title="Draft")
    unlisted = await create_course(client, teacher, title="Unlisted")
    await set_status(client, teacher, unlisted, "UNLISTED")

    page = await _find(client)

    assert [course["title"] for course in page["items"]] == ["Newer", "Older"]
    assert page["next_cursor"] is None
    # Each result says whose course it is.
    assert page["items"][0]["channel"]["slug"] == teacher.channel_slug


async def test_a_disabled_teachers_courses_are_not_listed(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    await _publish(client, teacher, "Python Basics")
    await db.execute(update(User).where(User.id == teacher.id).values(is_active=False))

    assert await _titles(client) == []
    assert await _titles(client, query="python") == []


# --- Searching ---------------------------------------------------------------------------


async def test_search_finds_title_matches_before_description_matches(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    # The title match is published first, so newest-first order would put it last:
    # only ranking by relevance gives the order below.
    await _publish(client, teacher, "Python Loops")
    await _publish(client, teacher, "Data Science", description="We use Python a lot.")
    await _publish(client, teacher, "Cooking 101", description="Recipes.")

    assert await _titles(client, query="python") == ["Python Loops", "Data Science"]


async def test_search_understands_word_forms(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    await _publish(client, teacher, "Looping in Python")

    # "loops" and "looping" share the stem "loop".
    assert await _titles(client, query="loops") == ["Looping in Python"]


async def test_search_box_syntax(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher = await make_teacher(client, db)
    for title in ("Python for Data", "Python for Web", "Java for Web"):
        await _publish(client, teacher, title)

    assert set(await _titles(client, query='"for web"')) == {
        "Python for Web",
        "Java for Web",
    }
    assert await _titles(client, query="python -web") == ["Python for Data"]
    assert set(await _titles(client, query="java or data")) == {
        "Java for Web",
        "Python for Data",
    }


@pytest.mark.parametrize("query", ["c++ & |", "!!!", "'", "(python", "the", "   "])
async def test_odd_input_never_breaks_search(
    client: httpx.AsyncClient, db: AsyncSession, query: str
) -> None:
    teacher = await make_teacher(client, db)
    await _publish(client, teacher, "C++ Basics")

    response = await client.get("/courses", params={"query": query})

    assert response.status_code == 200


async def test_search_finds_only_published_courses(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    await create_course(client, teacher, title="Python Draft")
    unlisted = await create_course(client, teacher, title="Python Unlisted")
    await set_status(client, teacher, unlisted, "UNLISTED")
    await _publish(client, teacher, "Python Published")

    assert await _titles(client, query="python") == ["Python Published"]


# --- Paging ------------------------------------------------------------------------------


async def test_browsing_page_by_page(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    for n in range(1, 6):
        await _publish(client, teacher, f"Course {n}")

    pages = await _every_page(client, limit=2)

    assert [[course["title"] for course in page] for page in pages] == [
        ["Course 5", "Course 4"],
        ["Course 3", "Course 2"],
        ["Course 1"],
    ]


async def test_new_courses_dont_shift_the_next_page(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    for n in range(1, 5):
        await _publish(client, teacher, f"Course {n}")
    first = await _find(client, limit=2)
    assert [course["title"] for course in first["items"]] == ["Course 4", "Course 3"]

    await _publish(client, teacher, "Course 5")  # published while the reader pages
    second = await _find(client, limit=2, cursor=first["next_cursor"])

    # With ?page=2 (OFFSET 2) this would be ["Course 3", "Course 2"]: Course 3 twice.
    assert [course["title"] for course in second["items"]] == ["Course 2", "Course 1"]


async def test_search_pages_through_results_with_equal_rank(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    ids = [(await _publish(client, teacher, "Python Basics"))["id"] for _ in range(5)]

    pages = await _every_page(client, query="python", limit=2)

    # Same title, so the same rank: the id decides, and every course shows up once.
    assert [len(page) for page in pages] == [2, 2, 1]
    assert [course["id"] for page in pages for course in page] == sorted(
        ids, reverse=True
    )


async def test_a_bad_cursor_is_400(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher = await make_teacher(client, db)
    for title in ("Python 1", "Python 2"):
        await _publish(client, teacher, title)
    browse_cursor = (await _find(client, limit=1))["next_cursor"]

    garbage = await client.get("/courses", params={"cursor": "garbage"})
    # A browsing cursor marks a place in "newest first", not in "best match".
    mixed = await client.get(
        "/courses", params={"query": "python", "cursor": browse_cursor}
    )

    for response in (garbage, mixed):
        assert response.status_code == 400
        assert response.json() == {
            "detail": "This page link is invalid. Start again from the first page."
        }


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 51}, {"query": "x" * 201}, {"cursor": "x" * 501}],
    ids=["limit-0", "limit-51", "query-too-long", "cursor-too-long"],
)
async def test_out_of_range_parameters_are_422(
    client: httpx.AsyncClient, params: dict[str, Any]
) -> None:
    assert (await client.get("/courses", params=params)).status_code == 422
