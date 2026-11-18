"""HTTP endpoints for courses. Thin on purpose: the rules live in service.py.

GET uses the slug (/courses/python-basics), the URL people share. Teacher actions use
the id (/courses/42), which never changes, even when the slug does.
"""

from fastapi import APIRouter, status

from app.core.db import DbSession
from app.core.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, SearchQuery
from app.modules.auth.deps import CurrentUser, OptionalUser
from app.modules.courses import service
from app.modules.courses.deps import OwnCourse, OwnLesson, OwnSection
from app.modules.courses.schemas import (
    CourseCard,
    CourseCreate,
    CourseOut,
    CoursePage,
    CourseSummary,
    CourseUpdate,
    LessonCreate,
    LessonMove,
    LessonOrder,
    LessonOut,
    LessonUpdate,
    SectionCreate,
    SectionOrder,
    SectionOut,
    SectionUpdate,
)

# No prefix: two routes sit under /channels, where a channel's course list belongs.
router = APIRouter(tags=["courses"])


@router.get("/courses")
async def find_courses(
    db: DbSession,
    query: SearchQuery = None,
    cursor: Cursor = None,
    limit: Limit = DEFAULT_LIMIT,
) -> Page[CourseCard]:
    """Browse published courses, newest first, or search them with ?query=, best
    match first. Pass next_cursor back as ?cursor= for the next page."""
    courses, next_cursor = await service.find_courses(db, query, cursor, limit)
    return Page[CourseCard](
        items=[CourseCard.model_validate(course) for course in courses],
        next_cursor=next_cursor,
    )


@router.post("/courses", status_code=status.HTTP_201_CREATED)
async def create_course(
    data: CourseCreate, user: CurrentUser, db: DbSession
) -> CourseOut:
    """Create a draft course in your channel."""
    return CourseOut.model_validate(await service.create_course(db, user, data))


@router.get("/courses/{slug}")
async def read_course(slug: str, viewer: OptionalUser, db: DbSession) -> CoursePage:
    """A course page with its outline. Sign-in is optional: a teacher sees their own
    drafts and unpublished lessons."""
    course = await service.get_visible_course(db, slug, viewer)
    sections = await service.get_outline(db, course, viewer)
    return CoursePage(
        **CourseOut.model_validate(course).model_dump(), sections=sections
    )


@router.patch("/courses/{course_id}")
async def update_course(
    data: CourseUpdate, course: OwnCourse, db: DbSession
) -> CourseOut:
    """Edit, publish or hide your course. Only the fields you send are changed."""
    return CourseOut.model_validate(await service.update_course(db, course, data))


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course: OwnCourse, db: DbSession) -> None:
    """Delete a course that was never published."""
    await service.delete_course(db, course)


# Declared before /channels/{slug}/courses, though "me" can't be a channel slug anyway.
@router.get("/channels/me/courses")
async def list_my_courses(user: CurrentUser, db: DbSession) -> list[CourseSummary]:
    """All your courses, drafts included, newest first."""
    courses = await service.list_my_courses(db, user)
    return [CourseSummary.model_validate(course) for course in courses]


@router.get("/channels/{slug}/courses")
async def list_channel_courses(slug: str, db: DbSession) -> list[CourseSummary]:
    """A channel's published courses, newest first. No sign-in needed."""
    courses = await service.list_channel_courses(db, slug)
    return [CourseSummary.model_validate(course) for course in courses]


# --- Sections ----------------------------------------------------------------------------


@router.post("/courses/{course_id}/sections", status_code=status.HTTP_201_CREATED)
async def add_section(
    data: SectionCreate, course: OwnCourse, db: DbSession
) -> SectionOut:
    """Add a section at the end of your course."""
    return SectionOut.model_validate(await service.add_section(db, course, data))


@router.patch("/sections/{section_id}")
async def update_section(
    data: SectionUpdate, section: OwnSection, db: DbSession
) -> SectionOut:
    return SectionOut.model_validate(await service.update_section(db, section, data))


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(section: OwnSection, db: DbSession) -> None:
    """Delete a section and every lesson in it."""
    await service.delete_section(db, section)


# --- Lessons -----------------------------------------------------------------------------


@router.post("/sections/{section_id}/lessons", status_code=status.HTTP_201_CREATED)
async def add_lesson(
    data: LessonCreate, section: OwnSection, db: DbSession
) -> LessonOut:
    """Add a draft lesson at the end of your section."""
    return LessonOut.model_validate(await service.add_lesson(db, section, data))


@router.patch("/lessons/{lesson_id}")
async def update_lesson(
    data: LessonUpdate, lesson: OwnLesson, db: DbSession
) -> LessonOut:
    """Edit, publish, schedule or unpublish your lesson."""
    return LessonOut.model_validate(await service.update_lesson(db, lesson, data))


@router.delete("/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(lesson: OwnLesson, db: DbSession) -> None:
    await service.delete_lesson(db, lesson)


# --- Reordering --------------------------------------------------------------------------


@router.put("/courses/{course_id}/sections/order")
async def reorder_sections(
    data: SectionOrder, course: OwnCourse, db: DbSession
) -> list[SectionOut]:
    """Send every section id of the course in the new order. 409 if the list doesn't
    match the course (reload and try again)."""
    sections = await service.reorder_sections(db, course, data.section_ids)
    return [SectionOut.model_validate(section) for section in sections]


@router.put("/sections/{section_id}/lessons/order")
async def reorder_lessons(
    data: LessonOrder, section: OwnSection, db: DbSession
) -> list[LessonOut]:
    """Send every lesson id of the section in the new order."""
    lessons = await service.reorder_lessons(db, section, data.lesson_ids)
    return [LessonOut.model_validate(lesson) for lesson in lessons]


@router.post("/lessons/{lesson_id}/move")
async def move_lesson(
    data: LessonMove, lesson: OwnLesson, user: CurrentUser, db: DbSession
) -> LessonOut:
    """Move a lesson to another place, in its section or another one of the course."""
    return LessonOut.model_validate(await service.move_lesson(db, user, lesson, data))
