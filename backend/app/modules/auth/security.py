"""Password hashing and token helpers.

Plain functions only: no database, no FastAPI, no HTTP. The security-critical code stays
small, in one place, and testable without starting anything.
"""

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

# --- Passwords ---------------------------------------------------------------------------

# argon2id with the library defaults (RFC 9106's low-memory profile: 3 passes over 64 MiB).
# If these settings are raised later, needs_rehash() flags old hashes so login can
# upgrade them.
_hasher = PasswordHasher()


async def hash_password(password: str) -> str:
    """Hash a password for storage. The result carries its own random salt and settings."""
    # Hashing is slow on purpose (about 50 ms of CPU). Running it in a worker thread lets
    # the event loop keep serving other requests meanwhile.
    return await to_thread.run_sync(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await to_thread.run_sync(_verify_password, password_hash, password)


def _verify_password(password_hash: str, password: str) -> bool:
    # argon2 raises on a wrong password instead of returning False. Only a mismatch is
    # caught: a corrupt hash in the database is a bug and should fail loudly, not look
    # like a wrong password.
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the hash was made with different settings from the current ones."""
    return _hasher.check_needs_rehash(password_hash)


# Login checks against this when the email doesn't exist, or the account has no password,
# so rejecting a missing account takes as long as rejecting a wrong password. Otherwise
# response times would reveal which emails are registered.
DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(16))


# --- Signed tokens (JWT) -----------------------------------------------------------------

_ALGORITHM = "HS256"


def encode_jwt(claims: dict[str, Any], *, token_type: str, lifetime: timedelta) -> str:
    """Sign `claims` with JWT_SECRET, adding `type`, `iat` and `exp`.

    `type` matters because one secret signs more than one kind of token (access tokens
    now, the Google sign-in state cookie later). Decoding checks it, so one kind of token
    can never be accepted as another.
    """
    now = datetime.now(UTC)
    payload = {**claims, "type": token_type, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_jwt(
    token: str, *, token_type: str, require: Iterable[str] = ()
) -> dict[str, Any]:
    """Check the signature, expiry and type, then return the claims.

    Raises jwt.InvalidTokenError (or a subclass, such as ExpiredSignatureError) when the
    token is not acceptable.
    """
    claims: dict[str, Any] = jwt.decode(
        token,
        _secret(),
        # Pinned here, never taken from the token's own header. Otherwise an attacker could
        # send an unsigned token with "alg": "none" and have it accepted.
        algorithms=[_ALGORITHM],
        options={"require": ["exp", "iat", "type", *require]},
    )
    if claims["type"] != token_type:
        raise jwt.InvalidTokenError(f"Expected a {token_type!r} token")
    return claims


def _secret() -> str:
    return settings.jwt_secret.get_secret_value()


# --- Access tokens -----------------------------------------------------------------------

ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: int
    # Informational, e.g. for the dashboard's token inspector. Route guards read roles
    # from the database, so a role change applies at once, not when the token expires.
    roles: tuple[str, ...]


def create_access_token(user_id: int, roles: Iterable[str]) -> str:
    return encode_jwt(
        # The JWT standard says `sub` is a string, and PyJWT rejects any other type.
        {"sub": str(user_id), "roles": sorted(str(role) for role in roles)},
        token_type=ACCESS_TOKEN_TYPE,
        lifetime=timedelta(minutes=settings.access_token_minutes),
    )


def decode_access_token(token: str) -> AccessTokenClaims:
    """Raises jwt.InvalidTokenError if the token is invalid, expired or not an access token."""
    claims = decode_jwt(token, token_type=ACCESS_TOKEN_TYPE, require=["sub"])
    try:
        user_id = int(claims["sub"])
    except ValueError as exc:
        raise jwt.InvalidTokenError("`sub` is not a user id") from exc
    return AccessTokenClaims(user_id=user_id, roles=tuple(claims.get("roles", ())))


# --- Opaque tokens (refresh tokens, email links) -----------------------------------------


def new_opaque_token() -> str:
    """A random, URL-safe token: 32 bytes (256 bits), far beyond guessing."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest (64 characters), the form tokens are stored in.

    A fast hash is fine here, unlike for passwords. The token is 256 random bits, so there
    is nothing to brute-force, and a hash that always gives the same output lets the
    database find the row through its unique index.
    """
    return hashlib.sha256(token.encode()).hexdigest()
