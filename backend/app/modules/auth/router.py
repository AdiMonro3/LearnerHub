"""HTTP endpoints under /auth. Thin on purpose: read the request, call service.py or
google.py, shape the response. The rules themselves live in service.py."""

from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm

from app.core.config import settings
from app.core.db import DbSession
from app.modules.auth import google, service
from app.modules.auth.deps import CurrentUser
from app.modules.auth.google import GoogleError
from app.modules.auth.schemas import (
    AuthResponse,
    ChangePasswordIn,
    EmailVerificationConfirmIn,
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    ProvidersOut,
    RegisterIn,
    TokenOut,
)
from app.modules.auth.service import (
    AccountDisabled,
    GoogleAccountConflict,
    InvalidRefreshToken,
    SessionTokens,
)
from app.modules.users.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
# The browser sends the refresh cookie only to /auth/* (refresh, logout, password
# change), not with every API request.
REFRESH_COOKIE_PATH = "/auth"


# --- Email and password ------------------------------------------------------------------


@router.get("/providers")
async def providers() -> ProvidersOut:
    """Which sign-in buttons the login page should show."""
    return ProvidersOut(password=True, google=google.is_configured())


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterIn, db: DbSession, response: Response) -> AuthResponse:
    user = await service.register(db, data)
    tokens = await service.start_session(db, user)
    return await _signed_in(db, response, user, tokens)


@router.post("/login")
async def login(data: LoginIn, db: DbSession, response: Response) -> AuthResponse:
    user = await service.authenticate(db, data.email, data.password.get_secret_value())
    tokens = await service.start_session(db, user)
    return await _signed_in(db, response, user, tokens)


@router.post("/token")
async def token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession
) -> TokenOut:
    """For Swagger's "Authorize" button: put your email in the "username" field.

    Returns an access token only. No refresh cookie or stored session, since Swagger
    can't use them.
    """
    user = await service.authenticate(db, form.username, form.password)
    await db.commit()  # keeps a re-hashed password, if authenticate() made one
    return TokenOut(access_token=service.access_token_for(user))


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    request: Request, response: Response, db: DbSession
) -> AuthResponse | JSONResponse:
    """Swap the refresh cookie for a new one plus a fresh access token. The frontend
    calls this on every page load to restore the session, and whenever a request gets
    a 401."""
    raw = request.cookies.get(REFRESH_COOKIE)
    try:
        if raw is None:
            raise InvalidRefreshToken
        user, tokens = await service.refresh_session(db, raw)
    except InvalidRefreshToken as exc:
        failed = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        _clear_refresh_cookie(failed)  # so the browser stops sending a dead cookie
        return failed
    return await _signed_in(db, response, user, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> None:
    """Sign this device out. Always succeeds, even when already signed out."""
    await service.end_session(db, request.cookies.get(REFRESH_COOKIE))
    _clear_refresh_cookie(response)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: ChangePasswordIn, user: CurrentUser, request: Request, db: DbSession
) -> None:
    """Change the password, or set a first one on a Google-only account. Every other
    device is signed out; this one stays signed in."""
    await service.change_password(
        db, user, data, current_refresh_token=request.cookies.get(REFRESH_COOKIE)
    )


# --- Email links -------------------------------------------------------------------------


@router.post("/verify-email/request", status_code=status.HTTP_204_NO_CONTENT)
async def request_email_verification(user: CurrentUser, db: DbSession) -> None:
    """Email the signed-in user a link that confirms their address."""
    await service.request_email_verification(db, user)


@router.post("/verify-email/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_email_verification(
    data: EmailVerificationConfirmIn, db: DbSession
) -> None:
    await service.confirm_email_verification(db, data.token)


@router.post("/password-reset/request", status_code=status.HTTP_204_NO_CONTENT)
async def request_password_reset(data: PasswordResetRequestIn, db: DbSession) -> None:
    """Email a reset link if the address has an account. Always 204, so this can't be
    used to find out who is registered."""
    await service.request_password_reset(db, data.email)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(data: PasswordResetConfirmIn, db: DbSession) -> None:
    """Set a new password from a reset link. Signs out every device."""
    await service.reset_password(db, data.token, data.new_password)


# --- Google ------------------------------------------------------------------------------


@router.get("/google/login")
async def google_login() -> RedirectResponse:
    """Start Google sign-in: the login page's Google button links here."""
    if not google.is_configured():
        return _to_login_page("google_not_configured")
    attempt = google.new_login_attempt()
    response = RedirectResponse(
        google.authorization_url(attempt), status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(
        google.STATE_COOKIE,
        google.encode_state_cookie(attempt),
        max_age=int(google.STATE_LIFETIME.total_seconds()),
        path=google.STATE_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        # Lax, not Strict: Google's redirect back to us is a navigation that starts on
        # another site, and browsers leave Strict cookies out of those.
        samesite="lax",
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: DbSession,
    http: Annotated[httpx.AsyncClient, Depends(google.get_http_client)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google redirects the browser here. Always answers with a redirect back to the
    frontend: /auth/callback on success, /login?error=<code> on failure."""
    try:
        if not google.is_configured():
            raise GoogleError("google_not_configured")
        if error is not None:  # e.g. the person pressed "Cancel" at Google
            code_ = (
                "google_access_denied" if error == "access_denied" else "google_failed"
            )
            raise GoogleError(code_)
        attempt = google.check_state(request.cookies.get(google.STATE_COOKIE), state)
        if not code:
            raise GoogleError("google_failed")
        id_token = await google.exchange_code(http, code, attempt.code_verifier)
        claims = await google.verify_id_token(id_token, attempt.nonce)
        user = await service.sign_in_with_google(db, claims)
        tokens = await service.start_session(db, user)
    except GoogleError as exc:
        response = _to_login_page(exc.code)
    except AccountDisabled:
        response = _to_login_page("account_disabled")
    except GoogleAccountConflict:
        response = _to_login_page("account_conflict")
    else:
        # Only the refresh cookie is set. The access token is never put in the URL,
        # where browser history, server logs and Referer headers would leak it. The
        # frontend's /auth/callback page calls /auth/refresh to get one.
        response = RedirectResponse(
            f"{settings.frontend_url}/auth/callback", status_code=status.HTTP_302_FOUND
        )
        _set_refresh_cookie(response, tokens.refresh_token)
    # Single use: whatever happened, this attempt is over.
    response.delete_cookie(
        google.STATE_COOKIE,
        path=google.STATE_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


# --- Helpers -----------------------------------------------------------------------------


async def _signed_in(
    db: DbSession, response: Response, user: User, tokens: SessionTokens
) -> AuthResponse:
    _set_refresh_cookie(response, tokens.refresh_token)
    return AuthResponse(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=await service.user_out(db, user),
    )


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # JavaScript can't read it, so an XSS bug can't steal it
        secure=settings.cookie_secure,
        # Left out of cross-site POSTs, so another site can't use your session (CSRF).
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    # Same path and flags as when it was set, or the browser keeps the old cookie.
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def _to_login_page(error_code: str) -> RedirectResponse:
    return RedirectResponse(
        f"{settings.frontend_url}/login?{urlencode({'error': error_code})}",
        status_code=status.HTTP_302_FOUND,
    )
