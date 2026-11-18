"""Courses and their outline (sections and lessons): creating, editing, publishing
and deleting them, and who may see what.

Who sees a course depends on its status:

    DRAFT      only its teacher
    UNLISTED   anyone with the link, but it's never listed or searchable
    PUBLISHED  everyone; listed on the channel page and in search

A disabled teacher's courses are hidden from everyone, like their channel. Anyone who
may not see a course gets 404, never 403, so nobody can learn that a draft exists.

Works on an AsyncSession and knows nothing about HTTP. Failures are AppError
subclasses. Functions that write commit before returning.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import ColumnElement, Select, and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, contains_eager

from app.core.db import commit_or_conflict
from app.core.errors import AppError, Conflict, NotFound
from app.core.pagination import CursorId, decode_cursor, paginate
from app.core.slugs import free_slug
from app.modules.channels import service as channels_service
from app.modules.channels.models import Channel
from app.modules.courses.models import (
    Course,
    CourseStatus,
    Lesson,
    LessonStatus,
    Section,
)
from app.modules.courses.schemas import (
    SLUG_MAX_LENGTH,
    CourseCreate,
    CourseUpdate,
    LessonCreate,
    LessonMove,
    LessonOut,
    LessonUpdate,
    OutlineSection,
    SectionCreate,
    SectionUpdate,
)
from app.modules.users.models import User

# --- Errors ------------------------------------------------------------------------------


class CourseNotFound(NotFound):
    detail = "Course not found."


class SlugTaken(Conflict):
    detail = "This course URL is already taken. Choose another."


class SectionNotFound(NotFound):
    detail = "Section not found."


class LessonNotFound(NotFound):
    detail = "Lesson not found."


class OrderMismatch(Conflict):
    detail = (
        "The list doesn't match what's there now: something was added or removed. "
        "Reload and try again."
    )


class MoveOutsideCourse(AppError):
    detail = "A lesson can only move to a section of the same course."


class PublishedCourseCannotBeDeleted(Conflict):
    detail = (
        "This course has been published, so it can't be deleted. "
        "Set it back to draft to hide it."
    )


# --- Teacher side ------------------------------------------------------------------------


async def create_course(db: AsyncSession, user: User, data: CourseCreate) -> Course:
    """A new DRAFT course in `user`'s channel. 404 if they have no channel yet."""
    channel = await channels_service.get_my_channel(db, user)
    slug = data.slug or await free_slug(
        db, Course.slug, data.title, max_length=SLUG_MAX_LENGTH, fallback="course"
    )
    course = Course(
        channel=channel,
        title=data.title,
        slug=slug,
        description=data.description,
        price_paise=data.price_paise,
        status=CourseStatus.DRAFT,
    )
    db.add(course)
    await _save(db)
    return course


async def get_own_course(db: AsyncSession, user: User, course_id: int) -> Course:
    """The course, if `user` teaches it. Everyone else gets CourseNotFound.

    The ownership check is part of the query (WHERE teacher_id = user), not a
    comparison after loading, so another teacher's course is never even fetched.
    """
    course = await db.scalar(
        select(Course)
        .join(Course.channel)
        .where(Course.id == course_id, Channel.teacher_id == user.id)
        .options(contains_eager(Course.channel))
    )
    if course is None:
        raise CourseNotFound
    return course


async def update_course(db: AsyncSession, course: Course, data: CourseUpdate) -> Course:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(course, field, value)
    # First time it becomes visible (PUBLISHED or UNLISTED): record when. Going back
    # to DRAFT keeps the date, which also marks the course as "was public once".
    if course.status != CourseStatus.DRAFT and course.published_at is None:
        course.published_at = datetime.now(UTC)
    await _save(db)
    return course


async def delete_course(db: AsyncSession, course: Course) -> None:
    """Delete a course that was never published, with its sections and lessons.

    A course that was ever public may have learners (subscriptions, progress and
    comments in later phases), so it can only be hidden by setting it back to DRAFT.
    """
    if course.published_at is not None:
        raise PublishedCourseCannotBeDeleted
    # The database deletes its sections and lessons (ON DELETE CASCADE).
    await db.delete(course)
    await db.commit()


async def list_my_courses(db: AsyncSession, user: User) -> Sequence[Course]:
    """Every course in `user`'s channel, whatever its status, newest first."""
    channel = await channels_service.get_my_channel(db, user)
    result = await db.scalars(
        select(Course)
        .where(Course.channel_id == channel.id)
        # id breaks ties: rows made in one transaction share the same created_at.
        .order_by(Course.created_at.desc(), Course.id.desc())
    )
    return result.all()


# --- Public side -------------------------------------------------------------------------


def _visible_to(viewer: User | None) -> ColumnElement[bool]:
    """SQL condition for the courses `viewer` may open. The query must join Course to
    its Channel and the Channel to its teacher (User)."""
    public = and_(Course.status != CourseStatus.DRAFT, User.is_active)
    if viewer is None:
        return public
    # A teacher sees all of their own courses, drafts included.
    return or_(Channel.teacher_id == viewer.id, public)


async def get_visible_course(
    db: AsyncSession, slug: str, viewer: User | None
) -> Course:
    """A course page, for anyone allowed to see it (see the module docstring)."""
    course = await db.scalar(
        select(Course)
        .join(Course.channel)
        .join(Channel.teacher)
        .where(Course.slug == slug, _visible_to(viewer))
        .options(contains_eager(Course.channel))
    )
    if course is None:
        raise CourseNotFound
    return course


async def list_channel_courses(db: AsyncSession, channel_slug: str) -> Sequence[Course]:
    """The courses listed on a channel's public page: PUBLISHED only, newest first.
    404 if the channel doesn't exist or its teacher is disabled."""
    channel = await channels_service.get_public_channel(db, channel_slug)
    result = await db.scalars(
        select(Course)
        .where(Course.channel_id == channel.id, Course.status == CourseStatus.PUBLISHED)
        .order_by(Course.published_at.desc(), Course.id.desc())
    )
    return result.all()


async def get_outline(
    db: AsyncSession, course: Course, viewer: User | None
) -> list[OutlineSection]:
    """The course's sections, each with its lessons, in order.

    Its teacher sees everything, drafts and scheduled lessons included. Everyone else
    sees only lessons that are published and due, and no section left empty by that.
    `course.channel` must be loaded, as get_visible_course() does.
    """
    is_its_teacher = viewer is not None and course.channel.teacher_id == viewer.id
    sections = await db.scalars(
        select(Section).where(Section.course_id == course.id).order_by(Section.position)
    )
    lesson_query = (
        select(Lesson)
        .join(Lesson.section)
        .where(Section.course_id == course.id)
        .order_by(Lesson.position)
    )
    if not is_its_teacher:
        lesson_query = lesson_query.where(_lesson_is_out())

    lessons: defaultdict[int, list[LessonOut]] = defaultdict(list)
    for lesson in await db.scalars(lesson_query):
        lessons[lesson.section_id].append(LessonOut.model_validate(lesson))
    return [
        OutlineSection(
            id=section.id,
            title=section.title,
            position=section.position,
            lessons=lessons[section.id],
        )
        for section in sections
        if is_its_teacher or lessons[section.id]
    ]


def _lesson_is_out() -> ColumnElement[bool]:
    """SQL condition for lessons learners may see: published, and due.

    "Due" is checked at read time, so a scheduled lesson appears by itself the moment
    its time comes; nothing has to flip a flag. The time comes from Python, like the
    published_at values it is compared with.
    """
    return and_(
        Lesson.status == LessonStatus.PUBLISHED,
        Lesson.published_at <= datetime.now(UTC),
    )


# --- Browse and search -------------------------------------------------------------------

# The two sort orders, each with its own cursor shape. A cursor from one can't be used
# with the other: decoding checks the tag ("new" or "rank").
_NEWEST_CURSOR = TypeAdapter(tuple[Literal["new"], AwareDatetime, CursorId])
_BEST_MATCH_CURSOR = TypeAdapter(tuple[Literal["rank"], float, CursorId])


async def find_courses(
    db: AsyncSession, query: str | None, cursor: str | None, limit: int
) -> tuple[list[Course], str | None]:
    """PUBLISHED courses of active teachers, one page at a time.

    Without `query`: newest first. With `query`: full-text search, best match first.
    Returns the page and the cursor of the next one (None on the last page).
    """
    listed = (
        select(Course)
        .join(Course.channel)
        .join(Channel.teacher)
        .where(Course.status == CourseStatus.PUBLISHED, User.is_active)
        .options(contains_eager(Course.channel))
    )
    if query := (query or "").strip():
        return await _best_matches(db, listed, query, cursor, limit)
    return await _newest(db, listed, cursor, limit)


async def _newest(
    db: AsyncSession, listed: Select[Course], cursor: str | None, limit: int
) -> tuple[list[Course], str | None]:
    if cursor is not None:
        _, published_at, last_id = decode_cursor(cursor, _NEWEST_CURSOR)
        listed = listed.where(
            tuple_(Course.published_at, Course.id) < tuple_(published_at, last_id)
        )
    rows = await db.scalars(
        listed.order_by(Course.published_at.desc(), Course.id.desc()).limit(limit + 1)
    )
    return paginate(
        rows.all(), limit, lambda course: ("new", course.published_at, course.id)
    )


async def _best_matches(
    db: AsyncSession,
    listed: Select[Course],
    query: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[Course], str | None]:
    # websearch_to_tsquery reads what people type into search boxes: plain words,
    # "exact phrases", -excluded words, OR. It never fails on odd input, unlike
    # to_tsquery, which raises a syntax error (a 500) on something like "c++ & |".
    # 'english' stems words, so "loops" also finds "loop" and "looping".
    tsquery = func.websearch_to_tsquery("english", query)
    # ts_rank weighs the title (weight A) above the description (weight B). The rank
    # goes into the cursor, and the next page asks for "rank < last rank": the float
    # comes back from JSON bit for bit, so equal ranks compare as equal and the id
    # decides between them.
    rank = func.ts_rank(Course.search_vector, tsquery)
    # @@ means "matches", and uses the GIN index on search_vector.
    matches = listed.add_columns(rank).where(
        Course.search_vector.bool_op("@@")(tsquery)
    )
    if cursor is not None:
        _, last_rank, last_id = decode_cursor(cursor, _BEST_MATCH_CURSOR)
        matches = matches.where(tuple_(rank, Course.id) < tuple_(last_rank, last_id))
    rows = (
        await db.execute(
            matches.order_by(rank.desc(), Course.id.desc()).limit(limit + 1)
        )
    ).all()
    page, next_cursor = paginate(rows, limit, lambda row: ("rank", row[1], row[0].id))
    return [row[0] for row in page], next_cursor


# --- Sections ----------------------------------------------------------------------------


async def get_own_section(db: AsyncSession, user: User, section_id: int) -> Section:
    """The section, if `user` teaches its course. Everyone else gets SectionNotFound."""
    section = await db.scalar(
        select(Section)
        .join(Section.course)
        .join(Course.channel)
        .where(Section.id == section_id, Channel.teacher_id == user.id)
    )
    if section is None:
        raise SectionNotFound
    return section


async def add_section(db: AsyncSession, course: Course, data: SectionCreate) -> Section:
    # Two requests adding a section at the same moment would otherwise both read the
    # same "last position" and both pick the next one. With the lock, the second waits,
    # then sees the first.
    await _lock_course(db, course.id)
    section = Section(
        course_id=course.id,
        title=data.title,
        position=await _next_position(
            db, Section.position, Section.course_id, course.id
        ),
    )
    db.add(section)
    await db.commit()
    return section


async def update_section(
    db: AsyncSession, section: Section, data: SectionUpdate
) -> Section:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(section, field, value)
    await db.commit()
    return section


async def delete_section(db: AsyncSession, section: Section) -> None:
    """Delete a section and its lessons (ON DELETE CASCADE). The positions of the
    sections after it keep their gap; order is what matters, not the numbers."""
    await db.delete(section)
    await db.commit()


# --- Lessons -----------------------------------------------------------------------------


async def get_own_lesson(db: AsyncSession, user: User, lesson_id: int) -> Lesson:
    """The lesson, if `user` teaches its course. Everyone else gets LessonNotFound."""
    lesson = await db.scalar(
        select(Lesson)
        .join(Lesson.section)
        .join(Section.course)
        .join(Course.channel)
        .where(Lesson.id == lesson_id, Channel.teacher_id == user.id)
    )
    if lesson is None:
        raise LessonNotFound
    return lesson


async def add_lesson(db: AsyncSession, section: Section, data: LessonCreate) -> Lesson:
    # Same reason as add_section(): lock the parent so positions can't collide.
    await _lock_sections(db, section.id)
    lesson = Lesson(
        section_id=section.id,
        type=data.type,
        title=data.title,
        description=data.description,
        position=await _next_position(
            db, Lesson.position, Lesson.section_id, section.id
        ),
        status=LessonStatus.DRAFT,
    )
    db.add(lesson)
    await db.commit()
    return lesson


async def update_lesson(db: AsyncSession, lesson: Lesson, data: LessonUpdate) -> Lesson:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(lesson, field, value)
    # Published without a time means "now". (A DRAFT may keep a planned time.)
    if lesson.status == LessonStatus.PUBLISHED and lesson.published_at is None:
        lesson.published_at = datetime.now(UTC)
    await db.commit()
    return lesson


async def delete_lesson(db: AsyncSession, lesson: Lesson) -> None:
    await db.delete(lesson)
    await db.commit()


# --- Reordering --------------------------------------------------------------------------
#
# Positions are rewritten as 0, 1, 2, ... in one transaction. Halfway through, two rows
# can share a position (swapping A and B: A takes B's number before B has moved). That
# is allowed because the UNIQUE (parent, position) constraints are DEFERRABLE INITIALLY
# DEFERRED: Postgres checks them once, at COMMIT, when the order is whole again.


async def reorder_sections(
    db: AsyncSession, course: Course, section_ids: list[int]
) -> list[Section]:
    """Put the course's sections in the given order. `section_ids` must name every
    section of the course exactly once."""
    # The same lock as add_section(), so no section can appear between reading the
    # list and saving the new order.
    await _lock_course(db, course.id)
    sections = await db.scalars(select(Section).where(Section.course_id == course.id))
    return await _apply_order(db, sections.all(), section_ids)


async def reorder_lessons(
    db: AsyncSession, section: Section, lesson_ids: list[int]
) -> list[Lesson]:
    """Put the section's lessons in the given order. `lesson_ids` must name every
    lesson of the section exactly once."""
    await _lock_sections(db, section.id)
    lessons = await db.scalars(select(Lesson).where(Lesson.section_id == section.id))
    return await _apply_order(db, lessons.all(), lesson_ids)


async def move_lesson(
    db: AsyncSession, user: User, lesson: Lesson, data: LessonMove
) -> Lesson:
    """Move a lesson to a place in a section of the same course (the same section
    too). Both sections are renumbered 0, 1, 2, ..., closing the gap it leaves."""
    target = await get_own_section(db, user, data.section_id)
    source_id = lesson.section_id
    await _lock_sections(db, source_id, target.id)
    source_course_id = await db.scalar(
        select(Section.course_id).where(Section.id == source_id)
    )
    # Learners get access per course, so a lesson moved into another course would
    # quietly change who can watch it. Not allowed.
    if target.course_id != source_course_id:
        raise MoveOutsideCourse

    others = await db.scalars(
        select(Lesson)
        .where(Lesson.section_id == target.id, Lesson.id != lesson.id)
        .order_by(Lesson.position)
    )
    new_order = list(others)
    # list.insert() puts anything past the end at the end, which is what we want.
    new_order.insert(len(new_order) if data.position is None else data.position, lesson)
    lesson.section_id = target.id
    _renumber(new_order)
    if source_id != target.id:
        left_behind = await db.scalars(
            select(Lesson)
            .where(Lesson.section_id == source_id, Lesson.id != lesson.id)
            .order_by(Lesson.position)
        )
        _renumber(left_behind.all())
    await db.commit()
    return lesson


async def _apply_order[Row: (Section, Lesson)](
    db: AsyncSession, rows: Sequence[Row], ids: list[int]
) -> list[Row]:
    by_id = {row.id: row for row in rows}
    # Same ids, each once: the length check catches duplicates ([a, a, b] vs {a, b}).
    # A mismatch usually means the client's copy is stale (another tab added a
    # section), so it's a 409: reload, then try again.
    if len(ids) != len(by_id) or set(ids) != by_id.keys():
        raise OrderMismatch
    ordered = [by_id[row_id] for row_id in ids]
    _renumber(ordered)
    await db.commit()
    return ordered


def _renumber(rows: Sequence[Section | Lesson]) -> None:
    for position, row in enumerate(rows):
        row.position = position


# --- Locks -------------------------------------------------------------------------------
#
# SELECT ... FOR UPDATE holds a row until this transaction ends. Every change to a
# list of sections (or lessons) first locks its course (or section), so such changes
# happen one at a time instead of overwriting each other's positions.


async def _lock_course(db: AsyncSession, course_id: int) -> None:
    await db.execute(select(Course.id).where(Course.id == course_id).with_for_update())


async def _lock_sections(db: AsyncSession, *section_ids: int) -> None:
    # Locked in id order. A move from A to B locking A then B, while a move from B
    # to A locks B then A, would leave each waiting for the other: a deadlock.
    await db.execute(
        select(Section.id)
        .where(Section.id.in_(section_ids))
        .order_by(Section.id)
        .with_for_update()
    )


async def _next_position(
    db: AsyncSession,
    position: InstrumentedAttribute[int],
    parent_id: InstrumentedAttribute[int],
    parent: int,
) -> int:
    """One past the highest position under `parent`; 0 when it has no children yet.

    MAX + 1 rather than COUNT: after a delete leaves a gap, COUNT could hand out a
    position that is still taken.
    """
    highest = await db.scalar(select(func.max(position)).where(parent_id == parent))
    return 0 if highest is None else highest + 1


async def _save(db: AsyncSession) -> None:
    await commit_or_conflict(db, {"uq_courses_slug": SlugTaken})
