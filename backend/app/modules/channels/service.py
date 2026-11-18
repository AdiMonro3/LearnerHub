"""Channels: a teacher's public page, one per teacher.

Works on an AsyncSession and knows nothing about HTTP. Failures are AppError
subclasses (app.core.errors). Functions that write commit before returning.

This module knows nothing about courses: the courses module depends on channels, never
the other way round, so the two can't end up importing each other.
"""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from app.core.db import commit_or_conflict
from app.core.errors import Conflict, NotFound, PermissionDenied
from app.core.pagination import CursorId, decode_cursor, paginate
from app.core.slugs import free_slug
from app.modules.channels.models import Channel
from app.modules.channels.schemas import (
    RESERVED_SLUGS,
    SLUG_MAX_LENGTH,
    ChannelCreate,
    ChannelUpdate,
)
from app.modules.users.models import Role, User, UserRole

# --- Errors ------------------------------------------------------------------------------


class ChannelNotFound(NotFound):
    detail = "Channel not found."


class NoChannelYet(NotFound):
    detail = "You don't have a channel yet."


class AlreadyHasChannel(Conflict):
    detail = "You already have a channel."


class SlugTaken(Conflict):
    detail = "This channel URL is already taken. Choose another."


class EmailNotVerified(PermissionDenied):
    detail = "Verify your email address before creating a channel."


# --- Channels ----------------------------------------------------------------------------


async def create_channel(db: AsyncSession, user: User, data: ChannelCreate) -> Channel:
    """Create `user`'s channel and make them a TEACHER, in one transaction.

    `user` must have its roles loaded, as current_user does.
    """
    # Teachers will receive payments (Phase 4), so the platform must be able to reach
    # them. Google accounts arrive already verified.
    if user.email_verified_at is None:
        raise EmailNotVerified

    slug = data.slug or await free_slug(
        db,
        Channel.slug,
        data.name,
        max_length=SLUG_MAX_LENGTH,
        fallback="channel",
        reserved=RESERVED_SLUGS,
    )
    channel = Channel(
        teacher=user,
        name=data.name,
        slug=slug,
        bio=data.bio,
        price_monthly_paise=data.price_monthly_paise,
    )
    db.add(channel)
    # Roles are read from the database on every request (auth/deps.py), so the new
    # role works at once. No new access token is needed.
    if not any(user_role.role == Role.TEACHER for user_role in user.roles):
        user.roles.append(UserRole(role=Role.TEACHER))

    # No "do you already have a channel?" query first: two requests at the same moment
    # could both pass it. The UNIQUE constraints decide. On a refusal everything is
    # rolled back, including the TEACHER role: no teachers without a channel.
    await _save(db)
    return channel


async def get_my_channel(db: AsyncSession, user: User) -> Channel:
    channel = await db.scalar(
        select(Channel)
        .where(Channel.teacher_id == user.id)
        .options(joinedload(Channel.teacher))
    )
    if channel is None:
        raise NoChannelYet
    return channel


async def update_my_channel(
    db: AsyncSession, user: User, data: ChannelUpdate
) -> Channel:
    channel = await get_my_channel(db, user)
    # exclude_unset: only the fields the client actually sent.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(channel, field, value)
    await _save(db)
    return channel


async def get_public_channel(db: AsyncSession, slug: str) -> Channel:
    """A channel by its URL, as anyone may see it. A disabled teacher's channel is
    hidden, as if it didn't exist."""
    channel = await db.scalar(
        select(Channel)
        .join(Channel.teacher)
        .where(Channel.slug == slug, User.is_active)
        # The JOIN already fetched the teacher's columns; fill channel.teacher from
        # them instead of running a second query.
        .options(contains_eager(Channel.teacher))
    )
    if channel is None:
        raise ChannelNotFound
    return channel


# Newest first: (created_at, id), both descending.
_NEWEST_CURSOR = TypeAdapter(tuple[Literal["new"], AwareDatetime, CursorId])


async def find_channels(
    db: AsyncSession, query: str | None, cursor: str | None, limit: int
) -> tuple[list[Channel], str | None]:
    """Channels of active teachers, newest first; with `query`, only those whose name
    contains it, ignoring case. Returns one page and the cursor of the next."""
    statement = (
        select(Channel)
        .join(Channel.teacher)
        .where(User.is_active)
        .options(contains_eager(Channel.teacher))
    )
    if query := (query or "").strip():
        # autoescape: "%" and "_" in the query are plain characters, not LIKE
        # wildcards, so searching "100%" finds "100% Python", not every channel.
        statement = statement.where(Channel.name.icontains(query, autoescape=True))
    if cursor is not None:
        _, created_at, last_id = decode_cursor(cursor, _NEWEST_CURSOR)
        statement = statement.where(
            tuple_(Channel.created_at, Channel.id) < tuple_(created_at, last_id)
        )
    rows = await db.scalars(
        statement.order_by(Channel.created_at.desc(), Channel.id.desc()).limit(
            limit + 1
        )
    )
    return paginate(rows.all(), limit, _newest_key)


def _newest_key(channel: Channel) -> tuple[str, datetime, int]:
    return ("new", channel.created_at, channel.id)


async def _save(db: AsyncSession) -> None:
    await commit_or_conflict(
        db,
        {"uq_channels_teacher_id": AlreadyHasChannel, "uq_channels_slug": SlugTaken},
    )
