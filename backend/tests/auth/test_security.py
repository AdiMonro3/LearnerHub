"""Unit tests for app/modules/auth/security.py. No database or Docker needed."""

import base64
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from argon2 import PasswordHasher
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, settings
from app.modules.auth import security
from app.modules.auth.security import AccessTokenClaims

# --- Passwords ---------------------------------------------------------------------------


async def test_password_hash_verifies_only_the_right_password() -> None:
    password_hash = await security.hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert await security.verify_password(password_hash, "correct horse battery staple")
    assert not await security.verify_password(
        password_hash, "Correct horse battery staple"
    )


async def test_same_password_hashes_differently_each_time() -> None:
    # Each hash has its own random salt, so equal passwords can't be spotted in the table.
    first = await security.hash_password("same-password")
    second = await security.hash_password("same-password")

    assert first != second


async def test_dummy_hash_matches_no_password() -> None:
    assert not await security.verify_password(security.DUMMY_PASSWORD_HASH, "")
    assert not await security.verify_password(security.DUMMY_PASSWORD_HASH, "password")


def test_needs_rehash_flags_hashes_made_with_old_settings() -> None:
    old = PasswordHasher(time_cost=1, memory_cost=8 * 1024).hash("password")
    current = PasswordHasher().hash("password")

    assert security.needs_rehash(old)
    assert not security.needs_rehash(current)


# --- Access tokens -----------------------------------------------------------------------


def _claims(**overrides: Any) -> dict[str, Any]:
    """A valid access-token payload, to sign by hand for the attack tests."""
    now = datetime.now(UTC)
    return {
        "sub": "42",
        "roles": ["LEARNER"],
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **overrides,
    }


def _b64url(data: dict[str, Any]) -> str:
    raw = json.dumps(data, default=lambda d: int(d.timestamp())).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_access_token_round_trip() -> None:
    token = security.create_access_token(42, ["TEACHER", "LEARNER"])

    assert security.decode_access_token(token) == AccessTokenClaims(
        user_id=42, roles=("LEARNER", "TEACHER")
    )


def test_access_token_expires_after_configured_minutes() -> None:
    token = security.create_access_token(42, [])
    claims = jwt.decode(token, options={"verify_signature": False})

    assert claims["exp"] - claims["iat"] == settings.access_token_minutes * 60


def test_expired_token_is_rejected() -> None:
    token = security.encode_jwt(
        {"sub": "42"}, token_type="access", lifetime=timedelta(seconds=-1)
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(token)


def test_edited_claims_break_the_signature() -> None:
    # Keep the real header and signature, swap in a payload claiming to be user 1, an admin.
    header, _, signature = security.create_access_token(42, ["LEARNER"]).split(".")
    forged_payload = _b64url(_claims(sub="1", roles=["ADMIN"]))

    with pytest.raises(jwt.InvalidSignatureError):
        security.decode_access_token(f"{header}.{forged_payload}.{signature}")


def test_token_signed_with_another_secret_is_rejected() -> None:
    token = jwt.encode(_claims(), secrets.token_urlsafe(32), algorithm="HS256")

    with pytest.raises(jwt.InvalidSignatureError):
        security.decode_access_token(token)


def test_unsigned_alg_none_token_is_rejected() -> None:
    # An attacker's unsigned token. PyJWT's types don't expect a None key, but "none"
    # tokens are made exactly that way.
    token = jwt.encode(_claims(), None, algorithm="none")  # type: ignore[arg-type]

    with pytest.raises(jwt.InvalidAlgorithmError):
        security.decode_access_token(token)


def test_other_token_types_are_not_access_tokens() -> None:
    # Same secret, same `sub`, but minted for another purpose (e.g. the Google state cookie).
    token = security.encode_jwt(
        {"sub": "42"}, token_type="oauth_state", lifetime=timedelta(minutes=10)
    )

    with pytest.raises(jwt.InvalidTokenError, match="'access'"):
        security.decode_access_token(token)


def test_access_token_without_sub_is_rejected() -> None:
    token = security.encode_jwt({}, token_type="access", lifetime=timedelta(minutes=5))

    with pytest.raises(jwt.MissingRequiredClaimError):
        security.decode_access_token(token)


def test_access_token_with_non_numeric_sub_is_rejected() -> None:
    token = security.encode_jwt(
        {"sub": "admin"}, token_type="access", lifetime=timedelta(minutes=5)
    )

    with pytest.raises(jwt.InvalidTokenError, match="not a user id"):
        security.decode_access_token(token)


# --- Opaque tokens -----------------------------------------------------------------------


def test_opaque_tokens_are_unique_and_url_safe() -> None:
    tokens = {security.new_opaque_token() for _ in range(100)}

    assert len(tokens) == 100
    # 32 bytes in unpadded base64url is 43 characters, all safe in cookies and URLs.
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", token) for token in tokens)


def test_hash_token_is_stable_and_fits_the_column() -> None:
    token = security.new_opaque_token()

    assert security.hash_token(token) == security.hash_token(token)
    assert security.hash_token(token) != security.hash_token(token + "x")
    # 64 hex characters, matching the String(64) token_hash column.
    assert re.fullmatch(r"[0-9a-f]{64}", security.hash_token(token))


# --- Secret configuration ----------------------------------------------------------------


def test_real_jwt_secret_is_required_outside_dev() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(env="prod")  # still the built-in dev secret
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(env="prod", jwt_secret=SecretStr("too-short"))

    Settings(env="prod", jwt_secret=SecretStr(secrets.token_urlsafe(32)))  # accepted
    Settings(env="dev")  # dev may use the built-in secret
