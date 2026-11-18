import enum
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, CreatedAtMixin, IdMixin, TimestampMixin


class Role(enum.StrEnum):
    LEARNER = "LEARNER"
    TEACHER = "TEACHER"
    ADMIN = "ADMIN"


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # CITEXT = case-insensitive text: "Adi@Gmail.com" and "adi@gmail.com" are the same
    # email, so the UNIQUE constraint blocks duplicate sign-ups that differ only in case.
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    # NULL for accounts created through Google that never set a password.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # A timestamp instead of a boolean: answers "is it verified?" and "since when?".
    email_verified_at: Mapped[datetime | None]
    # Disabling an account keeps its history (payments, comments) instead of deleting it.
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))

    # lazy="raise": touching user.roles without loading it first is an error, not a
    # hidden extra query. Load explicitly: select(User).options(selectinload(User.roles)).
    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user",
        lazy="raise",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserRole(CreatedAtMixin, Base):
    """One row per role a user holds. One account can be both LEARNER and TEACHER."""

    __tablename__ = "user_roles"

    # Composite primary key (user_id, role): a user can't hold the same role twice.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(primary_key=True)

    user: Mapped[User] = relationship(back_populates="roles", lazy="raise")
