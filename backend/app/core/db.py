"""Database setup shared by every module. It has four parts:

1. Connection and sessions: how the app talks to Postgres, one session per request.
2. Base and mixins: the parent class of every table, and the columns many tables share.
3. commit_or_conflict: turns "this value is already taken" into a clean API error.
4. BIGINT_MAX: the largest id a URL may contain.
"""

import enum
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, ClassVar

from fastapi import Depends
from sqlalchemy import BigInteger, DateTime, Enum, Identity, MetaData, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings
from app.core.errors import AppError

# --- 1. Connection and sessions ---------------------------------------------------

# The engine keeps a small pool of open connections to Postgres and reuses them,
# because opening a new connection for every request is slow.
engine = create_async_engine(settings.database_url)

# A session is one conversation with the database: run queries, change rows, then
# commit (save) or roll back (undo). expire_on_commit=False keeps a saved object's
# values readable after the commit, so a route can return the row it just saved.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Give each request its own session, and close it when the request ends.
    Anything the request didn't commit is thrown away at that point."""
    async with SessionLocal() as session:
        yield session


# A route gets a session by declaring a parameter `db: DbSession`; FastAPI calls
# get_session for it.
DbSession = Annotated[AsyncSession, Depends(get_session)]


# --- 2. Base and mixins -----------------------------------------------------------

# Gives every index and constraint a predictable name, such as "uq_users_email".
# Alembic needs those names to change or drop a constraint later, and part 3 below
# uses them to tell which rule a write broke. Without this, Postgres makes names up.
# ix = index, uq = unique, ck = check, fk = foreign key, pk = primary key.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The parent class of every table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Which SQL type to use when a model writes Mapped[datetime] or Mapped[SomeEnum]:
    type_annotation_map: ClassVar = {
        # timestamptz: an exact moment, stored in UTC, so time zones can't mix it up.
        datetime: DateTime(timezone=True),
        # A VARCHAR column plus a CHECK listing the allowed values. (Postgres's own
        # ENUM type is hard to change later in a migration.)
        enum.Enum: Enum(
            enum.Enum, native_enum=False, create_constraint=True, length=20
        ),
    }


class IdMixin:
    """Adds `id`, the primary key. Postgres numbers new rows 1, 2, 3, ... by itself
    (an IDENTITY column). BIGINT, so the numbers never run out."""

    # sort_order only decides where the column appears in the table: a low number puts
    # `id` first, high numbers put the timestamps last. (Otherwise mixin columns go last.)
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True, sort_order=-10
    )


class CreatedAtMixin:
    """Adds `created_at`, filled in by Postgres when the row is inserted.
    For rows that are never edited, such as login tokens."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), sort_order=10
    )


class TimestampMixin(CreatedAtMixin):
    """Adds `created_at` and `updated_at`; every UPDATE sets updated_at to now."""

    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=11
    )

    # Fetch the new updated_at as part of the UPDATE itself. Otherwise SQLAlchemy
    # would try to fetch it later, the first time the code reads it, and async code
    # can't do that: the request would crash with a "MissingGreenlet" error.
    __mapper_args__: ClassVar = {"eager_defaults": True}


# --- 3. "Already taken" errors ----------------------------------------------------


def violated_constraint(exc: IntegrityError) -> str | None:
    """The name of the rule a write broke, e.g. "uq_users_email"."""
    if exc.orig is None:
        return None
    # exc.orig wraps the database driver's (asyncpg's) own error, which carries
    # the constraint's name.
    driver_error = exc.orig.__cause__
    return getattr(driver_error, "constraint_name", None)


async def commit_or_conflict(
    db: AsyncSession, conflicts: dict[str, type[AppError]]
) -> None:
    """Save the changes. If one of the rules in `conflicts` refuses them, undo the
    changes and raise that rule's error instead.

    Example: commit_or_conflict(db, {"uq_courses_slug": SlugTaken})

    Why not check with a SELECT first? Two requests could both check at the same
    moment, both see "free", and both insert. The database's UNIQUE rule can't be
    tricked like that, so we let the database decide and translate its answer.
    """
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        name = violated_constraint(exc)
        if name is not None and name in conflicts:
            raise conflicts[name]()
        raise  # any other broken rule is a bug in our code: let it fail loudly


# --- 4. Ids from URLs -------------------------------------------------------------

# The largest number a BIGINT can hold. Ids taken from a URL are checked against it:
# a bigger number would crash the database driver (a 500 error) instead of simply
# finding nothing (a 404).
BIGINT_MAX = 2**63 - 1
