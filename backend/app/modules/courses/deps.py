"""Route guards for courses. Use as a parameter type:

@router.patch("/courses/{course_id}")
async def endpoint(course: OwnCourse): ...    # 404 unless you teach this course

OwnSection and OwnLesson do the same for /sections/{section_id} and /lessons/{lesson_id}.
"""

from typing import Annotated

from fastapi import Depends, Path

from app.core.db import BIGINT_MAX, DbSession
from app.modules.auth.deps import CurrentUser
from app.modules.courses import service
from app.modules.courses.models import Course, Lesson, Section

# An id from the URL. The upper bound turns /courses/99999999999999999999 into a 422;
# otherwise the number overflows BIGINT inside the database driver (a 500).
UrlId = Annotated[int, Path(gt=0, le=BIGINT_MAX)]


async def own_course(course_id: UrlId, user: CurrentUser, db: DbSession) -> Course:
    """The course in the URL, if the signed-in user teaches it.

    Everyone else gets 404, not 403: a 403 would confirm that a draft course exists.
    """
    return await service.get_own_course(db, user, course_id)


OwnCourse = Annotated[Course, Depends(own_course)]


async def own_section(section_id: UrlId, user: CurrentUser, db: DbSession) -> Section:
    return await service.get_own_section(db, user, section_id)


async def own_lesson(lesson_id: UrlId, user: CurrentUser, db: DbSession) -> Lesson:
    return await service.get_own_lesson(db, user, lesson_id)


OwnSection = Annotated[Section, Depends(own_section)]
OwnLesson = Annotated[Lesson, Depends(own_lesson)]
