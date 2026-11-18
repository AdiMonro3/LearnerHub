from datetime import datetime
from typing import Self

from pydantic import BaseModel

from app.modules.users.models import User


class UserOut(BaseModel):
    """The signed-in user, shaped like the `User` type in frontend/src/api.ts."""

    id: int
    email: str
    full_name: str | None
    avatar_url: str | None
    email_verified: bool
    has_password: bool
    google_linked: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User, *, google_linked: bool) -> Self:
        # The three flags are derived, so the frontend never sees the hash or timestamps
        # behind them. `google_linked` lives in another table, so the caller supplies it.
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            email_verified=user.email_verified_at is not None,
            has_password=user.password_hash is not None,
            google_linked=google_linked,
            created_at=user.created_at,
        )
