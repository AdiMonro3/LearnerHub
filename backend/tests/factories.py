"""Small helpers that insert valid rows, so each test only spells out what it's testing."""

import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Course, Lesson, Section, User
from app.modules.courses.models import LessonType

_counter = itertools.count(1)


async def make_user(db: AsyncSession, email: str | None = None) -> User:
    user = User(email=email or f"user{next(_counter)}@example.com")
    db.add(user)
    await db.flush()
    return user


async def make_channel(
    db: AsyncSession, teacher: User | None = None, **fields: Any
) -> Channel:
    n = next(_counter)
    channel = Channel(
        teacher_id=(teacher or await make_user(db)).id,
        name=fields.pop("name", f"Channel {n}"),
        slug=fields.pop("slug", f"channel-{n}"),
        **fields,
    )
    db.add(channel)
    await db.flush()
    return channel


async def make_course(
    db: AsyncSession, channel: Channel | None = None, **fields: Any
) -> Course:
    n = next(_counter)
    course = Course(
        channel_id=(channel or await make_channel(db)).id,
        title=fields.pop("title", f"Course {n}"),
        slug=fields.pop("slug", f"course-{n}"),
        **fields,
    )
    db.add(course)
    await db.flush()
    return course


async def make_section(db: AsyncSession, course: Course, position: int) -> Section:
    section = Section(
        course_id=course.id, title=f"Section {position}", position=position
    )
    db.add(section)
    await db.flush()
    return section


async def make_lesson(
    db: AsyncSession, section: Section, position: int, **fields: Any
) -> Lesson:
    lesson = Lesson(
        section_id=section.id,
        type=fields.pop("type", LessonType.VIDEO),
        title=fields.pop("title", f"Lesson {position}"),
        position=position,
        **fields,
    )
    db.add(lesson)
    await db.flush()
    return lesson


@contextmanager
def violates(constraint: str) -> Iterator[None]:
    """Assert that the block fails because of one specific, named database constraint.

    Checking the name (e.g. "uq_users_email") proves the right rule stopped the write,
    not some unrelated error.
    """
    with pytest.raises(IntegrityError) as exc_info:
        yield
    # SQLAlchemy wraps asyncpg's exception; the original carries the constraint name.
    original = exc_info.value.orig.__cause__ if exc_info.value.orig else None
    assert getattr(original, "constraint_name", None) == constraint, original
