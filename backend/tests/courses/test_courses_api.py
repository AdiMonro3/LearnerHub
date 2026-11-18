"""The courses API: creating, editing, publishing and deleting, and who sees what."""

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Lesson, Section
from app.modules.users.models import User
from tests.courses.helpers import create_course, set_status
from tests.factories import make_lesson, make_section
from tests.helpers import make_teacher, sign_up

# --- Creating ----------------------------------------------------------------------------


async def test_a_teacher_creates_a_draft_course(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)

    response = await client.post(
        "/courses",
        json={
            "title": "Python Basics",
            "description": "From zero.",
            "price_paise": 49_900,
        },
        headers=teacher.headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "python-basics"
    assert (body["status"], body["published_at"]) == ("DRAFT", None)
    assert (body["description"], body["price_paise"]) == ("From zero.", 49_900)
    assert body["channel"]["slug"] == teacher.channel_slug


async def test_creating_a_course_needs_a_channel(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    learner = await sign_up(client, db)

    response = await client.post(
        "/courses", json={"title": "Python Basics"}, headers=learner.headers
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "You don't have a channel yet."}


async def test_creating_a_course_needs_sign_in(client: httpx.AsyncClient) -> None:
    response = await client.post("/courses", json={"title": "Python Basics"})

    assert response.status_code == 401


async def test_course_urls_are_unique_across_all_channels(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    await create_course(client, await make_teacher(client, db))
    other = await make_teacher(client, db)

    assert (await create_course(client, other))["slug"] == "python-basics-2"

    response = await client.post(
        "/courses",
        json={"title": "Mine", "slug": "python-basics"},
        headers=other.headers,
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This course URL is already taken. Choose another."
    }


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"slug": "Python Basics"},
        {"slug": "x" * 121},
        {"price_paise": -1},
        {"price_paise": 10_000_001},
        {"description": "x" * 10_001},
    ],
    ids=lambda body: next(iter(body)) + "=" + str(next(iter(body.values())))[:12],
)
async def test_invalid_details_are_422(
    client: httpx.AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    teacher = await make_teacher(client, db)

    response = await client.post(
        "/courses", json={"title": "Python Basics", **body}, headers=teacher.headers
    )

    assert response.status_code == 422


# --- Who can see a course ----------------------------------------------------------------


async def test_a_draft_is_visible_only_to_its_teacher(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    url = f"/courses/{(await create_course(client, teacher))['slug']}"

    assert (await client.get(url, headers=teacher.headers)).status_code == 200

    # 404, not 403, for everyone else: they can't tell the draft exists.
    stranger = await client.get(url, headers=(await sign_up(client, db)).headers)
    assert stranger.status_code == 404
    assert stranger.json() == {"detail": "Course not found."}
    other_teacher = (await make_teacher(client, db)).headers
    assert (await client.get(url, headers=other_teacher)).status_code == 404
    assert (await client.get(url)).status_code == 404


@pytest.mark.parametrize("status", ["PUBLISHED", "UNLISTED"])
async def test_published_and_unlisted_courses_open_for_everyone(
    client: httpx.AsyncClient, db: AsyncSession, status: str
) -> None:
    teacher = await make_teacher(client, db)
    course = await set_status(
        client, teacher, await create_course(client, teacher), status
    )
    url = f"/courses/{course['slug']}"

    assert (await client.get(url)).status_code == 200
    stranger = (await sign_up(client, db)).headers
    assert (await client.get(url, headers=stranger)).status_code == 200


async def test_a_disabled_teachers_courses_are_hidden(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    await set_status(client, teacher, course, "PUBLISHED")
    await db.execute(update(User).where(User.id == teacher.id).values(is_active=False))

    assert (await client.get(f"/courses/{course['slug']}")).status_code == 404


async def test_an_unknown_course_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/courses/no-such-course")).status_code == 404


# --- Publishing --------------------------------------------------------------------------


async def test_publishing_records_when_and_going_back_to_draft_keeps_it(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)

    published = await set_status(client, teacher, course, "PUBLISHED")
    assert published["published_at"] is not None

    drafted = await set_status(client, teacher, course, "DRAFT")
    assert drafted["published_at"] == published["published_at"]
    assert (await client.get(f"/courses/{course['slug']}")).status_code == 404

    # Making it visible again doesn't change the date it was first published.
    unlisted = await set_status(client, teacher, course, "UNLISTED")
    assert unlisted["published_at"] == published["published_at"]


async def test_unlisting_a_new_course_also_records_published_at(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)

    course = await set_status(
        client, teacher, await create_course(client, teacher), "UNLISTED"
    )

    assert course["published_at"] is not None


# --- Editing -----------------------------------------------------------------------------


async def test_only_the_fields_sent_are_changed(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher, description="Old")

    async def patch(body: dict[str, Any]) -> dict[str, Any]:
        response = await client.patch(
            f"/courses/{course['id']}", json=body, headers=teacher.headers
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    changed = await patch({"price_paise": 9_900})
    assert (changed["title"], changed["description"]) == ("Python Basics", "Old")
    assert changed["price_paise"] == 9_900
    assert changed["status"] == "DRAFT"
    assert (await patch({"description": None}))["description"] is None
    # Retitling keeps the URL, so shared links keep working.
    assert (await patch({"title": "Python, Properly"}))["slug"] == course["slug"]


async def test_changing_the_url(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)

    response = await client.patch(
        f"/courses/{course['id']}", json={"slug": "py-101"}, headers=teacher.headers
    )

    assert response.json()["slug"] == "py-101"
    assert (
        await client.get("/courses/py-101", headers=teacher.headers)
    ).status_code == 200
    old = await client.get(f"/courses/{course['slug']}", headers=teacher.headers)
    assert old.status_code == 404


async def test_changing_to_a_taken_url_is_409(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    await create_course(client, teacher, slug="taken")
    course = await create_course(client, teacher)

    response = await client.patch(
        f"/courses/{course['id']}", json={"slug": "taken"}, headers=teacher.headers
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [{"title": None}, {"slug": None}, {"price_paise": None}, {"status": None}],
    ids=lambda body: next(iter(body)),
)
async def test_required_fields_cannot_be_set_to_null(
    client: httpx.AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)

    response = await client.patch(
        f"/courses/{course['id']}", json=body, headers=teacher.headers
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", next(iter(body))]


async def test_an_unknown_status_is_422(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)

    response = await client.patch(
        f"/courses/{course['id']}", json={"status": "ARCHIVED"}, headers=teacher.headers
    )

    assert response.status_code == 422


async def test_only_its_teacher_can_edit_or_delete_a_course(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    url = f"/courses/{course['id']}"
    other = await make_teacher(client, db)

    # Another teacher gets the same 404 as for a course that doesn't exist.
    patched = await client.patch(url, json={"title": "Mine now"}, headers=other.headers)
    assert patched.status_code == 404
    assert (await client.delete(url, headers=other.headers)).status_code == 404
    assert (await client.patch(url, json={"title": "Mine now"})).status_code == 401

    mine = await client.get(f"/courses/{course['slug']}", headers=teacher.headers)
    assert mine.json()["title"] == "Python Basics"


@pytest.mark.parametrize("course_id", ["0", "-1", "99999999999999999999", "abc"])
async def test_course_ids_out_of_range_are_422_not_500(
    client: httpx.AsyncClient, db: AsyncSession, course_id: str
) -> None:
    teacher = await make_teacher(client, db)

    response = await client.patch(
        f"/courses/{course_id}", json={"title": "x"}, headers=teacher.headers
    )

    assert response.status_code == 422


# --- Deleting ----------------------------------------------------------------------------


async def test_deleting_a_never_published_course_removes_its_contents(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    course_row = await db.get(Course, course["id"])
    assert course_row is not None
    section = await make_section(db, course_row, position=0)
    await make_lesson(db, section, position=0)

    response = await client.delete(f"/courses/{course['id']}", headers=teacher.headers)

    assert response.status_code == 204
    gone = await client.get(f"/courses/{course['slug']}", headers=teacher.headers)
    assert gone.status_code == 404
    assert await db.scalar(select(func.count()).select_from(Section)) == 0
    assert await db.scalar(select(func.count()).select_from(Lesson)) == 0


async def test_a_course_that_was_ever_published_cannot_be_deleted(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    await set_status(client, teacher, course, "PUBLISHED")
    await set_status(client, teacher, course, "DRAFT")

    response = await client.delete(f"/courses/{course['id']}", headers=teacher.headers)

    assert response.status_code == 409
    assert "Set it back to draft" in response.json()["detail"]
    still_there = await client.get(
        f"/courses/{course['slug']}", headers=teacher.headers
    )
    assert still_there.status_code == 200


# --- Lists -------------------------------------------------------------------------------


async def test_my_courses_lists_every_status_newest_first(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    first = await create_course(client, teacher, title="First")
    second = await set_status(
        client,
        teacher,
        await create_course(client, teacher, title="Second"),
        "PUBLISHED",
    )
    third = await set_status(
        client, teacher, await create_course(client, teacher, title="Third"), "UNLISTED"
    )
    await create_course(client, await make_teacher(client, db), title="Not mine")

    response = await client.get("/channels/me/courses", headers=teacher.headers)

    assert response.status_code == 200
    listed = [(course["slug"], course["status"]) for course in response.json()]
    assert listed == [
        (third["slug"], "UNLISTED"),
        (second["slug"], "PUBLISHED"),
        (first["slug"], "DRAFT"),
    ]


async def test_my_courses_without_a_channel_is_404(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    learner = await sign_up(client, db)

    response = await client.get("/channels/me/courses", headers=learner.headers)

    assert response.status_code == 404


async def test_a_channel_page_lists_only_published_courses_newest_first(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    older = await create_course(client, teacher, title="Older")
    await set_status(client, teacher, older, "PUBLISHED")
    newer = await create_course(client, teacher, title="Newer")
    await set_status(client, teacher, newer, "PUBLISHED")
    await create_course(client, teacher, title="Draft")
    unlisted = await create_course(client, teacher, title="Unlisted")
    await set_status(client, teacher, unlisted, "UNLISTED")

    response = await client.get(f"/channels/{teacher.channel_slug}/courses")

    assert response.status_code == 200
    assert [course["slug"] for course in response.json()] == ["newer", "older"]


async def test_a_missing_or_hidden_channel_has_no_course_list(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    assert (await client.get("/channels/nobody/courses")).status_code == 404

    teacher = await make_teacher(client, db)
    await db.execute(update(User).where(User.id == teacher.id).values(is_active=False))

    hidden = await client.get(f"/channels/{teacher.channel_slug}/courses")
    assert hidden.status_code == 404
