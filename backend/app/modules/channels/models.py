from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, IdMixin, TimestampMixin
from app.core.slugs import SLUG_PATTERN

if TYPE_CHECKING:  # for the type checker only; at runtime "User" is looked up by name
    from app.modules.users.models import User


class Channel(IdMixin, TimestampMixin, Base):
    """A teacher's public page. Subscribing to it unlocks all of that teacher's courses."""

    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint(
            "price_monthly_paise >= 0", name="price_monthly_paise_non_negative"
        ),
    )

    # UNIQUE: one channel per teacher. RESTRICT: a teacher who owns a channel can't be
    # hard-deleted (their subscribers and payments hang off it). Disable them instead.
    teacher_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    name: Mapped[str] = mapped_column(String(100))
    # The public URL: /channels/{slug}. The CHECK keeps it URL-safe at the database level.
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    bio: Mapped[str | None] = mapped_column(Text)
    # An S3 object key, not a URL. The API turns keys into (signed) URLs when responding,
    # so moving to a CDN later doesn't mean rewriting stored data.
    banner_key: Mapped[str | None] = mapped_column(String(512))
    # Money in paise (₹1 = 100 paise) as an integer: floats can't represent money exactly.
    # 0 means the channel subscription is free.
    price_monthly_paise: Mapped[int] = mapped_column(server_default=text("0"))

    # One-way: a channel knows its teacher, but the users module knows nothing about
    # channels. Load it explicitly (joinedload / contains_eager) when needed.
    teacher: Mapped["User"] = relationship(lazy="raise")
