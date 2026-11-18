"""Auth business logic: accounts, sessions, passwords, Google accounts, email links.

Works on an AsyncSession and knows nothing about HTTP: no requests, responses or
cookies. Failures are AuthError subclasses, each carrying the status and message the API
returns for it (app.core.errors.app_error_handler sends them). Every public function
that writes commits before returning, except register() and sign_in_with_google(),
whose rows are committed together with the session that start_session() creates next.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from pydantic import SecretStr
from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import violated_constraint
from app.core.email import send_email
from app.core.errors import AppError
from app.modules.auth.google import GoogleClaims
from app.modules.auth.models import (
    EmailToken,
    EmailTokenPurpose,
    OAuthAccount,
    OAuthProvider,
    RefreshToken,
)
from app.modules.auth.schemas import ChangePasswordIn, RegisterIn
from app.modules.auth.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_opaque_token,
    verify_password,
)
from app.modules.users.models import Role, User, UserRole
from app.modules.users.schemas import UserOut

# A refresh token replayed this soon after being rotated is treated as two tabs that
# refreshed at the same moment, not as theft. See refresh_session().
ROTATION_GRACE = timedelta(seconds=10)
VERIFY_EMAIL_LIFETIME = timedelta(hours=24)
PASSWORD_RESET_LIFETIME = timedelta(minutes=30)


# --- Errors ------------------------------------------------------------------------------


class AuthError(AppError):
    """Base class for auth failures. Lets callers catch every auth error at once."""


class EmailTaken(AuthError):
    status_code = 409
    detail = "An account with this email already exists."


class InvalidCredentials(AuthError):
    status_code = 401
    detail = "Incorrect email or password."


class AccountDisabled(AuthError):
    status_code = 403
    detail = "This account has been disabled."


class InvalidRefreshToken(AuthError):
    status_code = 401
    detail = "Your session has ended. Please sign in again."


class WrongCurrentPassword(AuthError):
    # 400, not 401: the frontend reads any 401 as "access token expired" and refreshes.
    status_code = 400
    detail = "Current password is incorrect."


class InvalidEmailLink(AuthError):
    status_code = 400
    detail = "This link is invalid or has expired."


class GoogleAccountConflict(AuthError):
    status_code = 409
    detail = "This email is already linked to a different Google account."


def _now() -> datetime:
    return datetime.now(UTC)


# --- Users -------------------------------------------------------------------------------


async def get_user(db: AsyncSession, user_id: int) -> User | None:
    """Load a user with their roles. Roles must be loaded up front: the relationship is
    lazy="raise", because a hidden lazy load would fail under async anyway."""
    return await db.scalar(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    # The column is CITEXT, so this matches "Ada@Example.com" to "ada@example.com".
    return await db.scalar(
        select(User).where(User.email == email).options(selectinload(User.roles))
    )


async def user_out(db: AsyncSession, user: User) -> UserOut:
    google_linked = await db.scalar(
        select(
            exists().where(
                OAuthAccount.user_id == user.id,
                OAuthAccount.provider == OAuthProvider.GOOGLE,
            )
        )
    )
    return UserOut.from_user(user, google_linked=bool(google_linked))


def access_token_for(user: User) -> str:
    return create_access_token(user.id, [user_role.role for user_role in user.roles])


# --- Email and password ------------------------------------------------------------------


async def register(db: AsyncSession, data: RegisterIn) -> User:
    user = User(
        email=data.email,
        full_name=data.full_name,
        password_hash=await hash_password(data.password.get_secret_value()),
        roles=[UserRole(role=Role.LEARNER)],
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # Caught from the database rather than checked first: a check-then-insert would
        # still let two simultaneous sign-ups with the same email through.
        if violated_constraint(exc) == "uq_users_email":
            raise EmailTaken from exc
        raise
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    user = await _get_user_by_email(db, email)
    if user is None or user.password_hash is None:
        # No account, or a Google-only one. Spend the same time a real check takes, so
        # response times don't reveal which emails are registered.
        await verify_password(DUMMY_PASSWORD_HASH, password)
        raise InvalidCredentials
    if not await verify_password(user.password_hash, password):
        raise InvalidCredentials
    # Checked only after the password, so only the account's owner learns it's disabled.
    if not user.is_active:
        raise AccountDisabled
    if needs_rehash(user.password_hash):
        # The hash settings were raised since this password was set. This is the one
        # moment the plain password is available to re-hash it. The caller commits.
        user.password_hash = await hash_password(password)
    return user


async def change_password(
    db: AsyncSession,
    user: User,
    data: ChangePasswordIn,
    *,
    current_refresh_token: str | None,
) -> None:
    """Change the password, or set a first one on a Google-only account."""
    if user.password_hash is not None:
        current = data.current_password
        if current is None or not await verify_password(
            user.password_hash, current.get_secret_value()
        ):
            raise WrongCurrentPassword
    user.password_hash = await hash_password(data.new_password.get_secret_value())
    # Sign out every other device, in case someone else knew the old password. The
    # device making this request (known by its refresh cookie) stays signed in.
    keep = await _family_of(db, current_refresh_token, user.id)
    await _revoke_sessions(db, user.id, except_family=keep)
    await db.commit()


# --- Sessions ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str
    refresh_token: (
        str  # the raw value, for the cookie; the database keeps only its hash
    )
    expires_in: int  # seconds until access_token expires


async def start_session(
    db: AsyncSession, user: User, *, family_id: uuid.UUID | None = None
) -> SessionTokens:
    """Sign `user` in: one access token, plus a refresh token that starts a new login
    chain ("family"). Shared by password and Google sign-in."""
    tokens, _ = await _issue(db, user, family_id=family_id or uuid.uuid4())
    await db.commit()
    return tokens


async def refresh_session(db: AsyncSession, raw: str) -> tuple[User, SessionTokens]:
    """Swap a refresh token for a new one plus a fresh access token (rotation).

    Each refresh token works once. If a used one comes back, someone holds a copy: the
    owner and a thief now have the same token and we can't tell them apart, so the
    whole family is revoked and both must sign in again.
    """
    now = _now()
    token = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(raw))
        # Lock the row: two refreshes with the same token queue up here instead of
        # both succeeding. populate_existing re-reads the row once the lock is held.
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if token is None or token.expires_at <= now:
        raise InvalidRefreshToken
    if token.revoked_at is not None and not await _is_rotation_race(db, token, now):
        await _revoke_family(db, token.family_id)
        await db.commit()
        raise InvalidRefreshToken

    user = await get_user(db, token.user_id)
    if user is None or not user.is_active:
        await _revoke_family(db, token.family_id)
        await db.commit()
        raise InvalidRefreshToken

    tokens, new_row = await _issue(db, user, family_id=token.family_id)
    if token.revoked_at is None:
        token.revoked_at = now
        token.replaced_by_id = new_row.id
    await db.commit()
    return user, tokens


async def end_session(db: AsyncSession, raw: str | None) -> None:
    """Log out this device: revoke its whole login chain. Other devices stay signed in."""
    if raw is None:
        return
    family_id = await db.scalar(
        select(RefreshToken.family_id).where(RefreshToken.token_hash == hash_token(raw))
    )
    if family_id is not None:
        await _revoke_family(db, family_id)
        await db.commit()


async def _issue(
    db: AsyncSession, user: User, *, family_id: uuid.UUID
) -> tuple[SessionTokens, RefreshToken]:
    raw = new_opaque_token()
    row = RefreshToken(
        user_id=user.id,
        family_id=family_id,
        token_hash=hash_token(raw),
        expires_at=_now() + timedelta(days=settings.refresh_token_days),
    )
    db.add(row)
    await db.flush()  # assigns row.id, which the rotated-out token points to
    tokens = SessionTokens(
        access_token=access_token_for(user),
        refresh_token=raw,
        expires_in=settings.access_token_minutes * 60,
    )
    return tokens, row


async def _is_rotation_race(
    db: AsyncSession, token: RefreshToken, now: datetime
) -> bool:
    """True if `token` was rotated moments ago and its login is still going.

    Opening two tabs at once sends the same cookie twice; the second request finds the
    token just rotated by the first. Treating that as theft would sign the user out at
    random. A logged-out or revoked family has no live token left, so this window can't
    bring a session back.
    """
    if (
        token.replaced_by_id is None
        or token.revoked_at is None
        or now - token.revoked_at > ROTATION_GRACE
    ):
        return False
    family_alive = await db.scalar(
        select(
            exists().where(
                RefreshToken.family_id == token.family_id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
    )
    return bool(family_alive)


async def _family_of(
    db: AsyncSession, raw: str | None, user_id: int
) -> uuid.UUID | None:
    if raw is None:
        return None
    return await db.scalar(
        select(RefreshToken.family_id).where(
            RefreshToken.token_hash == hash_token(raw),
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
    )


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def _revoke_sessions(
    db: AsyncSession, user_id: int, *, except_family: uuid.UUID | None = None
) -> None:
    """Sign the user out everywhere (except, optionally, one login chain)."""
    stmt = update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if except_family is not None:
        stmt = stmt.where(RefreshToken.family_id != except_family)
    await db.execute(stmt.values(revoked_at=_now()))


# --- Google ------------------------------------------------------------------------------


async def sign_in_with_google(db: AsyncSession, claims: GoogleClaims) -> User:
    """Find or create the account for a verified Google identity. The caller then calls
    start_session(), whose commit also saves any account or link created here."""
    try:
        user = await _find_or_create_google_user(db, claims)
        await db.flush()
    except IntegrityError:
        # Two first-time sign-ins for the same Google account raced (a double-click),
        # and the other one inserted first. Its rows exist now, so look again.
        await db.rollback()
        user = await _find_or_create_google_user(db, claims)
    if not user.is_active:
        raise AccountDisabled
    return user


async def _find_or_create_google_user(db: AsyncSession, claims: GoogleClaims) -> User:
    # (a) Seen this Google account before. Matched on `sub`, Google's permanent id, so
    # this still works after the person changes their Gmail address.
    linked_user_id = await db.scalar(
        select(OAuthAccount.user_id).where(
            OAuthAccount.provider == OAuthProvider.GOOGLE,
            OAuthAccount.provider_sub == claims.sub,
        )
    )
    if linked_user_id is not None:
        user = await get_user(db, linked_user_id)
        assert user is not None  # the link is deleted with its user (ON DELETE CASCADE)
        return user

    now = _now()
    user = await _get_user_by_email(db, claims.email)
    if user is None:
        # (d) First visit: a new account. Google has verified the email; no password.
        user = User(
            email=claims.email,
            full_name=claims.name,
            avatar_url=claims.picture,
            email_verified_at=now,
            roles=[UserRole(role=Role.LEARNER)],
        )
        db.add(user)
        await db.flush()  # assigns user.id for the link below
    else:
        already_linked = await db.scalar(
            select(
                exists().where(
                    OAuthAccount.user_id == user.id,
                    OAuthAccount.provider == OAuthProvider.GOOGLE,
                )
            )
        )
        if already_linked:
            # (b) This email's account belongs to a different Google account.
            raise GoogleAccountConflict
        if user.email_verified_at is None:
            # (c) Someone registered this email with a password but never proved they
            # own the inbox. Google just proved who does. Drop the unproven password and
            # sign out its sessions; otherwise an attacker could register your address
            # first and keep access after you link Google ("account pre-hijacking").
            user.password_hash = None
            user.email_verified_at = now
            await _revoke_sessions(db, user.id)
        # (b) A verified account with this email: link Google to it.
        user.full_name = user.full_name or claims.name
        user.avatar_url = user.avatar_url or claims.picture

    db.add(
        OAuthAccount(
            user_id=user.id, provider=OAuthProvider.GOOGLE, provider_sub=claims.sub
        )
    )
    return user


# --- Email links (verify email, reset password) -----------------------------------------


async def request_email_verification(db: AsyncSession, user: User) -> None:
    if user.email_verified_at is not None:
        return
    raw = await _new_email_token(
        db, user, EmailTokenPurpose.VERIFY_EMAIL, VERIFY_EMAIL_LIFETIME
    )
    await db.commit()  # before sending, so the link works as soon as it arrives
    await send_email(
        user.email,
        "Confirm your LearnerHub email address",
        "Confirm your email address by opening this link within 24 hours:\n"
        f"{_frontend_link('/verify-email', raw)}",
    )


async def confirm_email_verification(db: AsyncSession, raw: str) -> None:
    user = await _use_email_token(db, raw, EmailTokenPurpose.VERIFY_EMAIL)
    user.email_verified_at = user.email_verified_at or _now()
    await db.commit()


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Email a reset link if `email` has an active account. The caller answers the same
    either way, so this can't be used to find out who is registered."""
    user = await _get_user_by_email(db, email)
    if user is None or not user.is_active:
        return
    raw = await _new_email_token(
        db, user, EmailTokenPurpose.RESET_PASSWORD, PASSWORD_RESET_LIFETIME
    )
    await db.commit()
    await send_email(
        user.email,
        "Reset your LearnerHub password",
        "Choose a new password by opening this link within 30 minutes:\n"
        f"{_frontend_link('/reset-password', raw)}\n\n"
        "If you didn't ask for this, ignore this email; your password stays the same.",
    )


async def reset_password(db: AsyncSession, raw: str, new_password: SecretStr) -> None:
    user = await _use_email_token(db, raw, EmailTokenPurpose.RESET_PASSWORD)
    if not user.is_active:
        raise AccountDisabled
    user.password_hash = await hash_password(new_password.get_secret_value())
    # The link reached their inbox, which proves they own the address.
    user.email_verified_at = user.email_verified_at or _now()
    # Whoever knew the old password, perhaps an attacker, is signed out everywhere.
    await _revoke_sessions(db, user.id)
    await db.commit()


async def _new_email_token(
    db: AsyncSession, user: User, purpose: EmailTokenPurpose, lifetime: timedelta
) -> str:
    # Only the newest link works: asking again cancels the earlier unused ones.
    await db.execute(
        delete(EmailToken).where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
    )
    raw = new_opaque_token()
    db.add(
        EmailToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=_now() + lifetime,
        )
    )
    return raw


async def _use_email_token(
    db: AsyncSession, raw: str, purpose: EmailTokenPurpose
) -> User:
    """Mark an email link as used and return its user, if the link is still valid."""
    token = await db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_token(raw), EmailToken.purpose == purpose)
        .with_for_update()  # two clicks at once can't both use the link
        .execution_options(populate_existing=True)
    )
    if token is None or token.used_at is not None or token.expires_at <= _now():
        raise InvalidEmailLink
    token.used_at = _now()
    user = await get_user(db, token.user_id)
    assert user is not None  # the token is deleted with its user (ON DELETE CASCADE)
    return user


def _frontend_link(path: str, token: str) -> str:
    return f"{settings.frontend_url}{path}?{urlencode({'token': token})}"
