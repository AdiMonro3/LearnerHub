"""Request and response bodies for courses."""

from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.core.db import BIGINT_MAX
from app.core.schemas import NotNull, PricePaise
from app.core.slugs import validate_slug
from app.modules.courses.models import CourseStatus, LessonStatus, LessonType

TITLE_MAX_LENGTH = 200  # the title columns of courses, sections and lessons
SLUG_MAX_LENGTH = 120  # the courses.slug column
DESCRIPTION_MAX_LENGTH = 10_000
# Most items one reorder request may list: far more than any real course has, and it
# stops a request with a million ids.
MAX_ORDER_LENGTH = 500

# Used for course, section and lesson titles alike.
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH),
]
CourseSlug = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, max_length=SLUG_MAX_LENGTH),
    AfterValidator(validate_slug),
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH)
]


# --- Requests ----------------------------------------------------------------------------


class CourseCreate(BaseModel):
    """A new course always starts as a DRAFT that only its teacher can see."""

    title: Title
    # Leave it out to have one made from the title.
    slug: CourseSlug | None = None
    description: Description | None = None
    price_paise: PricePaise = 0

    @field_validator("description")
    @classmethod
    def _blank_description_means_none(cls, description: str | None) -> str | None:
        return description or None


class CourseUpdate(BaseModel):
    """Only the fields sent are changed. `"description": null` clears it.

    `status` publishes or hides the course: PUBLISHED (listed, searchable), UNLISTED
    (anyone with the link) or DRAFT (only you). Retitling keeps the URL.
    """

    # Every PATCH model refuses unknown fields: a typo like "titel" would otherwise be
    # silently ignored, and the client would get a 200 for a change that never happened.
    model_config = ConfigDict(extra="forbid")

    title: Annotated[Title | None, NotNull] = None
    slug: Annotated[CourseSlug | None, NotNull] = None
    description: Description | None = None
    price_paise: Annotated[PricePaise | None, NotNull] = None
    status: Annotated[CourseStatus | None, NotNull] = None

    @field_validator("description")
    @classmethod
    def _blank_description_means_none(cls, description: str | None) -> str | None:
        return description or None


class SectionCreate(BaseModel):
    """A new section goes after the course's existing sections."""

    title: Title


class SectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[Title | None, NotNull] = None


class LessonCreate(BaseModel):
    """A new lesson goes at the end of its section, as a DRAFT."""

    # Fixed once created: later phases hang a video (VIDEO) or a live session (LIVE)
    # off the lesson, and switching type would orphan it.
    type: LessonType
    title: Title
    description: Description | None = None

    @field_validator("description")
    @classmethod
    def _blank_description_means_none(cls, description: str | None) -> str | None:
        return description or None


class LessonUpdate(BaseModel):
    """Only the fields sent are changed.

    Publishing: `{"status": "PUBLISHED"}` publishes now. Adding a future
    `published_at` schedules it instead: learners see the lesson from that moment, with
    no background job involved. The time must include a timezone ("...Z" or "+05:30").
    """

    model_config = ConfigDict(extra="forbid")

    title: Annotated[Title | None, NotNull] = None
    description: Description | None = None
    status: Annotated[LessonStatus | None, NotNull] = None
    published_at: AwareDatetime | None = None

    @field_validator("description")
    @classmethod
    def _blank_description_means_none(cls, description: str | None) -> str | None:
        return description or None


class SectionOrder(BaseModel):
    """Every section of the course, in the new order."""

    section_ids: list[int] = Field(max_length=MAX_ORDER_LENGTH)


class LessonOrder(BaseModel):
    """Every lesson of the section, in the new order."""

    lesson_ids: list[int] = Field(max_length=MAX_ORDER_LENGTH)


class LessonMove(BaseModel):
    """Where to put a lesson: a section of the same course, and a place in it."""

    # Bounded like ids in URLs: this one is looked up in the database.
    section_id: Annotated[int, Field(gt=0, le=BIGINT_MAX)]
    # 0 = first. Left out, or past the end: last.
    position: Annotated[int, Field(ge=0)] | None = None


# --- Responses ---------------------------------------------------------------------------


class ChannelSummary(BaseModel):
    """Enough to show "by <channel>" and link to /channels/{slug}."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class CourseSummary(BaseModel):
    """A course in a list: the channel page, a teacher's dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    price_paise: int
    status: CourseStatus
    # When it was first made visible; it stays set if the course goes back to DRAFT.
    published_at: datetime | None


class CourseCard(CourseSummary):
    """A course in browse and search results, from any channel."""

    channel: ChannelSummary


class CourseOut(CourseSummary):
    """One course's page."""

    description: str | None
    channel: ChannelSummary
    created_at: datetime
    updated_at: datetime


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    position: int


class LessonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    section_id: int
    type: LessonType
    title: str
    description: str | None
    position: int
    status: LessonStatus
    # For a PUBLISHED lesson: when learners can see it. In the future = scheduled.
    published_at: datetime | None


class OutlineSection(SectionOut):
    lessons: list[LessonOut]


class CoursePage(CourseOut):
    """GET /courses/{slug}: the course plus its outline.

    Its teacher sees every section and lesson. Everyone else sees only lessons that
    are published and due (published_at has passed), and no empty sections.
    """

    sections: list[OutlineSection]
