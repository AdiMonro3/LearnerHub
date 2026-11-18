"""Sections and lessons: building a course's outline, and who sees which lessons."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import Channel, Course, Lesson, User
from app.modules.courses import service
from app.modules.courses.schemas import SectionCreate
from tests.courses.helpers import (
    add_lesson,
    add_section,
    create_course,
    outline,
    patch_lesson,
    set_status,
)
from tests.helpers import Teacher, make_teacher, sign_up


async def _published_course(
    client: httpx.AsyncClient, db: AsyncSession
) -> tuple[Teacher, dict[str, Any]]:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    await set_status(client, teacher, course, "PUBLISHED")
    return teacher, course


# --- Sections ----------------------------------------------------------------------------


async def test_sections_are_added_at_the_end(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)

    sections = [
        await add_section(client, teacher, course, title)
        for title in ("Basics", "Loops", "Functions")
    ]

    assert [section["position"] for section in sections] == [0, 1, 2]
    assert await outline(client, course, teacher.headers) == [
        ("Basics", []),
        ("Loops", []),
        ("Functions", []),
    ]


async def test_after_a_delete_new_sections_still_go_last(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    await add_section(client, teacher, course, "Basics")
    middle = await add_section(client, teacher, course, "Loops")
    await add_section(client, teacher, course, "Functions")

    deleted = await client.delete(f"/sections/{middle['id']}", headers=teacher.headers)
    new = await add_section(client, teacher, course, "Classes")

    assert deleted.status_code == 204
    assert new["position"] == 3  # not 2, which would just be the count
    assert await outline(client, course, teacher.headers) == [
        ("Basics", []),
        ("Functions", []),
        ("Classes", []),
    ]


async def test_renaming_a_section(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher = await make_teacher(client, db)
    section = await add_section(
        client, teacher, await create_course(client, teacher), "Basics"
    )
    url = f"/sections/{section['id']}"

    renamed = await client.patch(url, json={"title": "Start"}, headers=teacher.headers)
    assert renamed.json() == {**section, "title": "Start"}

    assert (
        await client.patch(url, json={"title": None}, headers=teacher.headers)
    ).status_code == 422
    # Unknown fields are refused, so a typo can't look like a successful change.
    assert (
        await client.patch(url, json={"titel": "Typo"}, headers=teacher.headers)
    ).status_code == 422


async def test_deleting_a_section_deletes_its_lessons(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    section = await add_section(
        client, teacher, await create_course(client, teacher), "Basics"
    )
    await add_lesson(client, teacher, section, "Intro")
    await add_lesson(client, teacher, section, "Setup")

    response = await client.delete(
        f"/sections/{section['id']}", headers=teacher.headers
    )

    assert response.status_code == 204
    assert await db.scalar(select(func.count()).select_from(Lesson)) == 0


# --- Lessons -----------------------------------------------------------------------------


async def test_lessons_are_numbered_within_their_own_section(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    basics = await add_section(client, teacher, course, "Basics")
    loops = await add_section(client, teacher, course, "Loops")

    intro = await add_lesson(client, teacher, basics, "Intro")
    setup = await add_lesson(client, teacher, basics, "Setup", type="LIVE")
    for_loops = await add_lesson(client, teacher, loops, "For loops")

    assert [intro["position"], setup["position"], for_loops["position"]] == [0, 1, 0]
    assert (intro["type"], setup["type"]) == ("VIDEO", "LIVE")
    assert (intro["status"], intro["published_at"]) == ("DRAFT", None)


@pytest.mark.parametrize(
    "body",
    [
        {"type": "AUDIO", "title": "Intro"},
        {"title": "No type"},
        {"type": "VIDEO", "title": "   "},
    ],
    ids=["unknown-type", "no-type", "blank-title"],
)
async def test_invalid_lessons_are_422(
    client: httpx.AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    teacher = await make_teacher(client, db)
    section = await add_section(
        client, teacher, await create_course(client, teacher), "Basics"
    )

    response = await client.post(
        f"/sections/{section['id']}/lessons", json=body, headers=teacher.headers
    )

    assert response.status_code == 422


async def test_a_lessons_type_cannot_be_changed(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    section = await add_section(
        client, teacher, await create_course(client, teacher), "Basics"
    )
    lesson = await add_lesson(client, teacher, section, "Intro")

    response = await client.patch(
        f"/lessons/{lesson['id']}", json={"type": "LIVE"}, headers=teacher.headers
    )

    assert response.status_code == 422


async def test_publishing_a_lesson_shows_it_to_learners(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course = await _published_course(client, db)
    section = await add_section(client, teacher, course, "Basics")
    lesson = await add_lesson(client, teacher, section, "Intro")
    assert await outline(client, course) == []  # a draft lesson, so nothing to show

    published = await patch_lesson(client, teacher, lesson, status="PUBLISHED")

    assert published["published_at"] is not None  # "now"
    assert await outline(client, course) == [("Basics", ["Intro"])]


async def test_a_scheduled_lesson_appears_when_its_time_comes(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course = await _published_course(client, db)
    section = await add_section(client, teacher, course, "Basics")
    lesson = await add_lesson(client, teacher, section, "Intro")
    next_week = datetime.now(UTC) + timedelta(days=7)

    scheduled = await patch_lesson(
        client, teacher, lesson, status="PUBLISHED", published_at=next_week.isoformat()
    )

    assert datetime.fromisoformat(scheduled["published_at"]) == next_week
    assert await outline(client, course) == []
    assert await outline(client, course, teacher.headers) == [("Basics", ["Intro"])]

    # A week later. No job flips anything: the time is checked on every read.
    await db.execute(
        update(Lesson)
        .where(Lesson.id == lesson["id"])
        .values(published_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert await outline(client, course) == [("Basics", ["Intro"])]


async def test_a_time_without_a_timezone_is_422(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    section = await add_section(
        client, teacher, await create_course(client, teacher), "Basics"
    )
    lesson = await add_lesson(client, teacher, section, "Intro")

    # 10:00 where? India and the server's clock would disagree by 5.5 hours.
    response = await client.patch(
        f"/lessons/{lesson['id']}",
        json={"status": "PUBLISHED", "published_at": "2030-01-01T10:00:00"},
        headers=teacher.headers,
    )

    assert response.status_code == 422


async def test_unpublishing_a_lesson_hides_it_again(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course = await _published_course(client, db)
    section = await add_section(client, teacher, course, "Basics")
    lesson = await add_lesson(client, teacher, section, "Intro")
    await patch_lesson(client, teacher, lesson, status="PUBLISHED")

    await patch_lesson(client, teacher, lesson, status="DRAFT")

    assert await outline(client, course) == []


async def test_learners_see_only_lessons_that_are_out(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher, course = await _published_course(client, db)
    basics = await add_section(client, teacher, course, "Basics")
    await patch_lesson(
        client,
        teacher,
        await add_lesson(client, teacher, basics, "Intro"),
        status="PUBLISHED",
    )
    await add_lesson(client, teacher, basics, "Setup")  # draft
    loops = await add_section(client, teacher, course, "Loops")
    await add_lesson(client, teacher, loops, "For loops")  # draft
    functions = await add_section(client, teacher, course, "Functions")
    await patch_lesson(
        client,
        teacher,
        await add_lesson(client, teacher, functions, "Def"),
        status="PUBLISHED",
        published_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )

    assert await outline(client, course, teacher.headers) == [
        ("Basics", ["Intro", "Setup"]),
        ("Loops", ["For loops"]),
        ("Functions", ["Def"]),
    ]
    # Learners: no drafts, nothing scheduled, and no sections left empty by that.
    learner = await sign_up(client, db)
    assert await outline(client, course, learner.headers) == [("Basics", ["Intro"])]
    assert await outline(client, course) == [("Basics", ["Intro"])]


async def test_deleting_a_lesson(client: httpx.AsyncClient, db: AsyncSession) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    section = await add_section(client, teacher, course, "Basics")
    intro = await add_lesson(client, teacher, section, "Intro")
    await add_lesson(client, teacher, section, "Setup")

    response = await client.delete(f"/lessons/{intro['id']}", headers=teacher.headers)

    assert response.status_code == 204
    assert await outline(client, course, teacher.headers) == [("Basics", ["Setup"])]


# --- Ownership ---------------------------------------------------------------------------


async def test_only_the_courses_teacher_can_change_itsoutline(
    client: httpx.AsyncClient, db: AsyncSession
) -> None:
    teacher = await make_teacher(client, db)
    course = await create_course(client, teacher)
    section = await add_section(client, teacher, course, "Basics")
    lesson = await add_lesson(client, teacher, section, "Intro")
    other = await make_teacher(client, db)

    attempts: list[tuple[str, str, dict[str, Any] | None]] = [
        ("POST", f"/courses/{course['id']}/sections", {"title": "Mine"}),
        ("PATCH", f"/sections/{section['id']}", {"title": "Mine"}),
        ("DELETE", f"/sections/{section['id']}", None),
        ("POST", f"/sections/{section['id']}/lessons", {"type": "VIDEO", "title": "x"}),
        ("PATCH", f"/lessons/{lesson['id']}", {"title": "Mine"}),
        ("DELETE", f"/lessons/{lesson['id']}", None),
    ]
    for method, url, body in attempts:
        as_other = await client.request(method, url, json=body, headers=other.headers)
        assert as_other.status_code == 404, (method, url)
        assert (await client.request(method, url, json=body)).status_code == 401

    assert await outline(client, course, teacher.headers) == [("Basics", ["Intro"])]


@pytest.mark.parametrize(
    "url",
    [
        "/sections/0",
        "/sections/99999999999999999999",
        "/lessons/-1",
        "/lessons/99999999999999999999",
    ],
)
async def test_ids_out_of_range_are_422_not_500(
    client: httpx.AsyncClient, db: AsyncSession, url: str
) -> None:
    teacher = await make_teacher(client, db)

    response = await client.patch(url, json={"title": "x"}, headers=teacher.headers)

    assert response.status_code == 422


# --- Two requests at once ----------------------------------------------------------------


async def test_two_sections_added_at_the_same_moment_both_succeed(
    engine: AsyncEngine,
) -> None:
    """Two real transactions running at once, so unlike the other tests this one
    commits its rows, and deletes them again at the end.

    Without the lock in add_section(), both transactions read "no sections yet", both
    pick position 0, and the second fails at COMMIT on the UNIQUE (course_id, position)
    constraint.
    """
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        user = User(email="race-test@example.com")
        db.add(user)
        await db.flush()
        channel = Channel(teacher_id=user.id, name="Race", slug="race-test")
        db.add(channel)
        await db.flush()
        course = Course(channel_id=channel.id, title="Race", slug="race-test")
        db.add(course)
        await db.commit()

    async def add(title: str) -> int:
        async with sessions() as db:
            own_copy = await db.get(Course, course.id)
            assert own_copy is not None
            section = await service.add_section(
                db, own_copy, SectionCreate(title=title)
            )
            return section.position

    try:
        positions = await asyncio.gather(add("A"), add("B"))
        assert sorted(positions) == [0, 1]
    finally:
        async with sessions() as db:
            await db.execute(delete(Course).where(Course.id == course.id))
            await db.execute(delete(Channel).where(Channel.id == channel.id))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
