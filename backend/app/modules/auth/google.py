"""Google sign-in: the OAuth 2.0 Authorization Code flow with PKCE, plus OpenID Connect.

This module only talks to Google. Matching the Google identity to a LearnerHub account
is service.sign_in_with_google(); the two endpoints are in router.py.

1. /auth/google/login makes three random values (state, nonce, PKCE verifier), keeps
   them in a signed, short-lived cookie and redirects the browser to Google.
2. The person signs in at Google, which redirects back to /auth/google/callback with a
   one-time `code` and the same `state`.
3. The callback checks `state` against the cookie, swaps the code (plus the PKCE
   verifier) for an ID token, then checks the ID token's signature and claims.
"""

import base64
import hashlib
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from anyio import to_thread

from app.core.config import settings
from app.modules.auth.security import decode_jwt, encode_jwt

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
# Google's ID tokens use either spelling.
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

STATE_COOKIE = "google_oauth"
STATE_COOKIE_PATH = "/auth/google"  # sent only to the two Google endpoints
STATE_LIFETIME = timedelta(minutes=10)  # time allowed to finish signing in at Google
_STATE_TOKEN_TYPE = "google_oauth_state"

# Google's public signing keys, downloaded on first use and cached. Tests replace this.
jwks_client = jwt.PyJWKClient(JWKS_URL, timeout=10)


class GoogleError(Exception):
    """Sign-in failed. `code` is one of the codes frontend/src/pages/Login.tsx explains."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GoogleClaims:
    """The verified facts from Google's ID token that LearnerHub uses."""

    sub: str  # Google's permanent id for the person; unlike the email, it never changes
    email: str
    name: str | None
    picture: str | None


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    # Ties the callback to the browser that started the sign-in. Without it, an attacker
    # could send you a callback link carrying *their* code and sign you into their
    # account ("login CSRF").
    state: str
    # Google copies it into the ID token, so an ID token from another sign-in can't be
    # replayed into this one.
    nonce: str
    # PKCE: only its hash goes to Google at the start. The code is useless without the
    # verifier itself, so a code leaked from a URL or log can't be redeemed by anyone else.
    code_verifier: str


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def new_login_attempt() -> LoginAttempt:
    # token_urlsafe(32) gives 43 characters, the minimum PKCE allows for a verifier.
    return LoginAttempt(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(32),
    )


def code_challenge(code_verifier: str) -> str:
    """PKCE "S256": base64url(sha256(verifier)) without "=" padding (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(attempt: LoginAttempt) -> str:
    """Google's sign-in page, for this attempt."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": attempt.state,
        "nonce": attempt.nonce,
        "code_challenge": code_challenge(attempt.code_verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",  # always let the person pick which Google account
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def encode_state_cookie(attempt: LoginAttempt) -> str:
    # Signed, so nobody can plant their own values; it expires with the attempt.
    return encode_jwt(
        {
            "state": attempt.state,
            "nonce": attempt.nonce,
            "code_verifier": attempt.code_verifier,
        },
        token_type=_STATE_TOKEN_TYPE,
        lifetime=STATE_LIFETIME,
    )


def check_state(cookie: str | None, state: str | None) -> LoginAttempt:
    """The login attempt this callback belongs to. Raises GoogleError("invalid_state")
    if the cookie is missing, forged or expired, or doesn't match `state`."""
    if not cookie or not state:
        raise GoogleError("invalid_state")
    try:
        claims = decode_jwt(
            cookie,
            token_type=_STATE_TOKEN_TYPE,
            require=["state", "nonce", "code_verifier"],
        )
    except jwt.InvalidTokenError as exc:
        raise GoogleError("invalid_state") from exc
    # Constant-time comparison, so response timing can't reveal the expected value.
    if not secrets.compare_digest(state.encode(), claims["state"].encode()):
        raise GoogleError("invalid_state")
    return LoginAttempt(
        state=claims["state"],
        nonce=claims["nonce"],
        code_verifier=claims["code_verifier"],
    )


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """FastAPI dependency: an HTTP client for calling Google. Tests override it with one
    that answers like Google, without the network."""
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def exchange_code(
    client: httpx.AsyncClient, code: str, code_verifier: str
) -> str:
    """Swap the one-time authorization code for Google's ID token."""
    secret = settings.google_client_secret
    try:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": settings.google_client_id,
                "client_secret": secret.get_secret_value() if secret else "",
                "redirect_uri": settings.google_redirect_uri,
            },
        )
        response.raise_for_status()
        id_token = response.json().get("id_token")
    except (httpx.HTTPError, ValueError) as exc:  # network error, non-2xx, or not JSON
        raise GoogleError("google_failed") from exc
    if not isinstance(id_token, str):
        raise GoogleError("google_failed")
    return id_token


async def verify_id_token(id_token: str, nonce: str) -> GoogleClaims:
    """Check that the ID token really comes from Google, for this app and this sign-in."""
    try:
        # PyJWKClient fetches Google's keys with blocking I/O: keep it off the event loop.
        signing_key = await to_thread.run_sync(
            jwks_client.get_signing_key_from_jwt, id_token
        )
        claims: dict[str, Any] = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],  # Google signs with RS256; accept nothing else
            audience=settings.google_client_id,  # issued to this app, not another one
            issuer=ISSUERS,
            options={"require": ["iss", "aud", "exp", "iat", "sub", "email"]},
        )
    except jwt.PyJWTError as exc:  # bad signature, expired, wrong audience, no keys...
        raise GoogleError("google_failed") from exc
    if not secrets.compare_digest(
        str(claims.get("nonce", "")).encode(), nonce.encode()
    ):
        raise GoogleError("google_failed")
    if claims.get("email_verified") is not True:
        raise GoogleError("email_not_verified")
    return GoogleClaims(
        sub=str(claims["sub"]),
        email=str(claims["email"]),
        name=_clip(claims.get("name"), 200),  # users.full_name is String(200)
        picture=_clip(claims.get("picture"), 2048),  # users.avatar_url is String(2048)
    )


def _clip(value: object, max_length: int) -> str | None:
    return value[:max_length] if isinstance(value, str) and value else None
