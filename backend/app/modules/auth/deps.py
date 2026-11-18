"""Route guards. Use them as parameter types or dependencies:

async def endpoint(user: CurrentUser): ...    # 401 unless signed in
async def endpoint(user: OptionalUser): ...   # None for anonymous visitors
@router.post("/admin/...", dependencies=[Depends(require_role(Role.ADMIN))])
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.db import DbSession
from app.modules.auth.security import decode_access_token
from app.modules.auth.service import AccountDisabled, get_user
from app.modules.users.models import Role, User

# Reads "Authorization: Bearer <token>". tokenUrl wires up Swagger's "Authorize" button.
# auto_error=False: a missing header gives None, so optional_user can allow anonymous.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def _not_signed_in() -> HTTPException:
    # 401, never 403, for a missing or expired token: the frontend answers a 401 by
    # refreshing the access token and retrying the request.
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Not signed in, or the session has expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def optional_user(
    db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> User | None:
    """The signed-in user, or None when the request has no token at all.

    A token that is present but bad or expired is still a 401, not "anonymous": the
    frontend then refreshes and retries, and the retry runs as the signed-in user.
    """
    if token is None:
        return None
    try:
        claims = decode_access_token(token)
    except jwt.InvalidTokenError:
        raise _not_signed_in() from None
    # The token proves who is asking; the database says what they may do now. Loading
    # the user each time (one primary-key lookup) means a disabled account or changed
    # role takes effect at once, not when the 15-minute token runs out.
    user = await get_user(db, claims.user_id)
    if user is None:
        raise _not_signed_in()
    if not user.is_active:
        raise AccountDisabled
    return user


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise _not_signed_in()
    return user


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(optional_user)]


def require_role(*roles: Role) -> Callable[..., Awaitable[User]]:
    """A dependency that lets through only users holding at least one of `roles`."""

    async def check(user: CurrentUser) -> User:
        if not any(user_role.role in roles for user_role in user.roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to do this.",
            )
        return user

    return check
