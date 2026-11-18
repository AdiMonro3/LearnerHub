"""Database rules for the channels, courses, sections and lessons tables."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Course, Lesson, Section, User
from app.modules.courses.models import CourseStatus, LessonStatus
from tests.factories import (
    make_channel,
    make_course,
    make_lesson,
    make_section,
    make_user,
    violates,
)

# --- channels -------------------------------------------------------------------------


async def test_teacher_has_at_most_one_channel(db: AsyncSession) -> None:
    teacher = await make_user(db)
    await make_channel(db, teacher)
    with violates("uq_channels_teacher_id"):
        await make_channel(db, teacher)


async def test_channel_slug_is_unique(db: AsyncSession) -> None:
    await make_channel(db, slug="python-with-adi")
    with violates("uq_channels_slug"):
        await make_channel(db, slug="python-with-adi")


@pytest.mark.parametrize(
    "bad_slug",
    ["Has-Caps", "has space", "-leading", "trailing-", "double--hyphen", "under_score"],
)
async def test_slug_must_be_url_safe(db: AsyncSession, bad_slug: str) -> None:
    with violates("ck_channels_slug_format"):
        await make_channel(db, slug=bad_slug)


async def test_teacher_who_owns_a_channel_cannot_be_deleted(db: AsyncSession) -> None:
    channel = await make_channel(db)
    with violates("fk_channels_teacher_id_users"):
        await db.execute(delete(User).where(User.id == channel.teacher_id))


async def test_channel_with_courses_cannot_be_deleted(db: AsyncSession) -> None:
    course = await make_course(db)
    with violates("fk_courses_channel_id_channels"):
        await db.execute(delete(Channel).where(Channel.id == course.channel_id))


# --- courses --------------------------------------------------------------------------


async def test_course_slug_is_unique(db: AsyncSession) -> None:
    await make_course(db, slug="python-basics")
    with violates("uq_courses_slug"):
        await make_course(db, slug="python-basics")


async def test_course_price_cannot_be_negative(db: AsyncSession) -> None:
    with violates("ck_courses_price_paise_non_negative"):
        await make_course(db, price_paise=-100)


async def test_new_course_defaults_to_free_draft(db: AsyncSession) -> None:
    course = await make_course(db)
    assert course.status == CourseStatus.DRAFT
    assert course.price_paise == 0


@pytest.mark.parametrize("status", [CourseStatus.PUBLISHED, CourseStatus.UNLISTED])
async def test_visible_course_must_record_published_at(
    db: AsyncSession, status: CourseStatus
) -> None:
    with violates("ck_courses_published_has_published_at"):
        await make_course(db, status=status, published_at=None)


async def test_search_finds_courses_by_title_and_description_words(
    db: AsyncSession,
) -> None:
    course = await make_course(
        db, title="Python for Beginners", description="Learn loops and functions"
    )

    async def search(query: str) -> list[int]:
        matches = Course.search_vector.op("@@")(
            func.websearch_to_tsquery("english", query)
        )
        return list(await db.scalars(select(Course.id).where(matches)))

    assert await search("python") == [course.id]
    # English stemming: "function" matches the stored word "functions".
    assert await search("function") == [course.id]
    assert await search("javascript") == []


# --- sections and lessons -------------------------------------------------------------


async def test_deleting_course_deletes_its_sections_and_lessons(
    db: AsyncSession,
) -> None:
    course = await make_course(db)
    section = await make_section(db, course, position=0)
    await make_lesson(db, section, position=0)

    await db.execute(delete(Course).where(Course.id == course.id))

    assert await db.scalar(select(func.count()).select_from(Section)) == 0
    assert await db.scalar(select(func.count()).select_from(Lesson)) == 0


async def test_section_positions_can_be_swapped_in_one_transaction(
    db: AsyncSession,
) -> None:
    course = await make_course(db)
    first = await make_section(db, course, position=0)
    second = await make_section(db, course, position=1)

    first.position, second.position = 1, 0
    await db.flush()
    # Deferred constraints are normally checked at COMMIT. The test never commits (it
    # rolls back), so force the check now: it passes because the end state is valid.
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    assert (first.position, second.position) == (1, 0)


async def test_duplicate_section_position_is_caught_when_constraints_are_checked(
    db: AsyncSession,
) -> None:
    course = await make_course(db)
    await make_section(db, course, position=0)
    second = await make_section(db, course, position=1)

    second.position = 0
    await db.flush()  # no error yet: the unique check is deferred...
    with violates("uq_sections_course_id_position"):
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # ...until now


async def test_duplicate_lesson_position_is_caught_when_constraints_are_checked(
    db: AsyncSession,
) -> None:
    section = await make_section(db, await make_course(db), position=0)
    await make_lesson(db, section, position=0)
    await make_lesson(db, section, position=0)
    with violates("uq_lessons_section_id_position"):
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_published_lesson_must_record_published_at(db: AsyncSession) -> None:
    section = await make_section(db, await make_course(db), position=0)
    with violates("ck_lessons_published_has_published_at"):
        await make_lesson(db, section, position=0, status=LessonStatus.PUBLISHED)


async def test_lesson_published_in_the_future_is_allowed(db: AsyncSession) -> None:
    # "Publish later": PUBLISHED with a future published_at is valid; the API decides
    # visibility with published_at <= now().
    section = await make_section(db, await make_course(db), position=0)
    later = datetime(2030, 1, 1, tzinfo=UTC)
    lesson = await make_lesson(
        db, section, position=0, status=LessonStatus.PUBLISHED, published_at=later
    )
    assert lesson.published_at == later
