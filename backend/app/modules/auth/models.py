import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin, IdMixin

# The auth module points at users only through foreign keys ("users.id"), never by
# importing the User class, so the two modules stay independent.


class OAuthProvider(enum.StrEnum):
    GOOGLE = "GOOGLE"


class OAuthAccount(IdMixin, CreatedAtMixin, Base):
    """Links a user to an external login, e.g. their Google account."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        # One Google account can belong to only one LearnerHub user...
        UniqueConstraint("provider", "provider_sub"),
        # ...and one user can link only one Google account.
        UniqueConstraint("user_id", "provider"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    provider: Mapped[OAuthProvider]
    # Google's permanent user id (the ID token's "sub" claim). Emails can change; this can't.
    provider_sub: Mapped[str] = mapped_column(String(255))


class RefreshToken(IdMixin, CreatedAtMixin, Base):
    """A long-lived login token. Only its SHA-256 hash is stored, never the token itself."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Every token issued by rotation from one login shares a family_id. If an old token
    # is replayed (a sign it was stolen), the whole family is revoked at once.
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # SHA-256 in hex is always 64 characters. Unique, so a lookup finds exactly one row.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    # The token this one was rotated into. Set together with revoked_at on each refresh.
    replaced_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )


class EmailTokenPurpose(enum.StrEnum):
    VERIFY_EMAIL = "VERIFY_EMAIL"
    RESET_PASSWORD = "RESET_PASSWORD"


class EmailToken(IdMixin, CreatedAtMixin, Base):
    """Single-use token sent by email, for verifying an address or resetting a password."""

    __tablename__ = "email_tokens"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[EmailTokenPurpose]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    # Set when the link is clicked, so the same link can't be used twice.
    used_at: Mapped[datetime | None]
