import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.slugs import SLUG_PATTERN

if (
    TYPE_CHECKING
):  # for the type checker only; at runtime "Channel" is looked up by name
    from app.modules.channels.models import Channel


class CourseStatus(enum.StrEnum):
    DRAFT = "DRAFT"  # only the teacher sees it
    PUBLISHED = "PUBLISHED"  # listed and searchable
    UNLISTED = "UNLISTED"  # reachable by link, not listed or searchable


class Course(IdMixin, TimestampMixin, Base):
    """One course, which the spec calls a "Room"."""

    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint("price_paise >= 0", name="price_paise_non_negative"),
        # A course that has ever been made visible must record when.
        CheckConstraint(
            "status = 'DRAFT' OR published_at IS NOT NULL",
            name="published_has_published_at",
        ),
        # GIN is the index type for full-text search: it maps each word to the rows
        # containing it, so a search doesn't scan every course.
        Index("ix_courses_search_vector", "search_vector", postgresql_using="gin"),
    )

    # RESTRICT: a channel with courses can't be deleted by accident.
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    # Globally unique, because the public URL is /courses/{slug}.
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_key: Mapped[str | None] = mapped_column(String(512))
    price_paise: Mapped[int] = mapped_column(server_default=text("0"))
    status: Mapped[CourseStatus] = mapped_column(
        server_default=CourseStatus.DRAFT.value
    )
    published_at: Mapped[datetime | None]

    # Postgres computes and stores this from title (weight A, ranks higher) and
    # description (weight B) on every insert/update, so search never goes stale.
    # Never needed in Python, so it's not loaded (and raises if accessed by mistake).
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(description, '')), 'B')",
            persisted=True,
        ),
        deferred=True,
        deferred_raiseload=True,
    )

    # One-way, like Channel.teacher: courses know their channel, channels don't know
    # their courses.
    channel: Mapped["Channel"] = relationship(lazy="raise")
    sections: Mapped[list["Section"]] = relationship(
        back_populates="course",
        order_by="Section.position",
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Section(IdMixin, TimestampMixin, Base):
    """A module/chapter inside a course, e.g. "Week 1: Basics"."""

    __tablename__ = "sections"
    __table_args__ = (
        # DEFERRABLE INITIALLY DEFERRED: uniqueness is checked at COMMIT, not after each
        # statement. That lets a reorder swap positions 0 and 1 in one transaction
        # without hitting a duplicate halfway through.
        UniqueConstraint(
            "course_id", "position", deferrable=True, initially="DEFERRED"
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    # CASCADE: deleting a course deletes its sections (and, below, their lessons).
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("courses.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int]

    course: Mapped[Course] = relationship(back_populates="sections", lazy="raise")
    lessons: Mapped[list["Lesson"]] = relationship(
        back_populates="section",
        order_by="Lesson.position",
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class LessonType(enum.StrEnum):
    VIDEO = "VIDEO"  # recorded; its file lives in video_assets (Phase 3)
    LIVE = "LIVE"  # a live class; its session lives in live_sessions (Phase 6)


class LessonStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class Lesson(IdMixin, TimestampMixin, Base):
    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint(
            "section_id", "position", deferrable=True, initially="DEFERRED"
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(
            "status = 'DRAFT' OR published_at IS NOT NULL",
            name="published_has_published_at",
        ),
    )

    section_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sections.id", ondelete="CASCADE")
    )
    type: Mapped[LessonType]
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int]
    # "Publish later" needs no extra status: a PUBLISHED lesson becomes visible once
    # published_at <= now(). No background job has to flip it at the right moment.
    status: Mapped[LessonStatus] = mapped_column(
        server_default=LessonStatus.DRAFT.value
    )
    published_at: Mapped[datetime | None]

    section: Mapped[Section] = relationship(back_populates="lessons", lazy="raise")
