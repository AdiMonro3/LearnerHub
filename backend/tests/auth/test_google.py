"""Google sign-in, with Google played by a fake: no network, no real Google account.

FakeGoogle answers the token request the way Google does, with an ID token signed by
a test RSA key, and serves that key's public half in place of Google's published keys.
"""

import base64
import hashlib
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.main import app
from app.modules.auth import google
from app.modules.auth.security import hash_password
from app.modules.users.models import User
from tests.auth.helpers import PASSWORD, login, refresh, refresh_cookie, register
from tests.factories import make_user

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
GOOGLE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ATTACKER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeGoogle:
    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}  # changes to the next ID token's claims
        self.signing_key = GOOGLE_KEY
        self.token_status = 200
        # What Google remembers from the sign-in page our app sent the browser to:
        self.nonce = ""
        self.code_challenge = ""
        self.token_requests: list[dict[str, list[str]]] = []

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        """Stands in for PyJWKClient: returns Google's public key."""
        return SimpleNamespace(key=GOOGLE_KEY.public_key())

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Google's token endpoint."""
        assert str(request.url) == google.TOKEN_URL
        form = parse_qs(request.content.decode())
        self.token_requests.append(form)
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        # Like Google, redeem the code only with the verifier whose hash came first.
        verifier = form["code_verifier"][0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        if challenge.rstrip(b"=").decode() != self.code_challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "google-id-1",
            "email": "ada@example.com",
            "email_verified": True,
            "name": "Ada Lovelace",
            "picture": "https://example.com/ada.png",
            "iat": now,
            "exp": now + 3600,
            "nonce": self.nonce,
            **self.claims,
        }
        id_token = jwt.encode(claims, self.signing_key, algorithm="RS256")
        return httpx.Response(200, json={"id_token": id_token})


@pytest.fixture
def fake_google(
    monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(google, "jwks_client", fake)

    async def http_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as c:
            yield c

    app.dependency_overrides[google.get_http_client] = http_client
    yield fake  # the client fixture removes the override afterwards


async def start(client: httpx.AsyncClient, fake: FakeGoogle) -> dict[str, str]:
    """Click "Continue with Google". Returns what our app asked Google for."""
    response = await client.get("/auth/google/login")
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    params = {name: values[0] for name, values in query.items()}
    fake.nonce, fake.code_challenge = params["nonce"], params["code_challenge"]
    return params


async def sign_in(
    client: httpx.AsyncClient, fake: FakeGoogle, **claims: Any
) -> httpx.Response:
    """The whole round trip: start, "sign in at Google", land on the callback."""
    params = await start(client, fake)
    fake.claims = claims
    return await client.get(
        "/auth/google/callback",
        params={"code": "one-time-code", "state": params["state"]},
    )


def login_page_with(error: str) -> str:
    return f"{settings.frontend_url}/login?error={error}"


async def signed_in_user(client: httpx.AsyncClient) -> dict[str, Any]:
    """What the frontend's /auth/callback page does next: refresh with the new cookie."""
    response = await client.post("/auth/refresh")
    assert response.status_code == 200
    user: dict[str, Any] = response.json()["user"]
    return user


# --- Starting ----------------------------------------------------------------------------


async def test_login_redirects_to_google(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    response = await client.get("/auth/google/login")

    assert response.status_code == 302
    url = urlsplit(response.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == google.AUTHORIZE_URL
    params = {name: values[0] for name, values in parse_qs(url.query).items()}
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == settings.google_redirect_uri
    assert params["response_type"] == "code"
    assert params["scope"] == "openid email profile"
    assert params["code_challenge_method"] == "S256"
    assert "code_verifier" not in params  # only its hash goes to Google
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{google.STATE_COOKIE}=")
    for attribute in ("HttpOnly", "Path=/auth/google", "SameSite=lax", "Max-Age=600"):
        assert attribute in cookie


async def test_providers_offer_google_once_configured(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    response = await client.get("/auth/providers")

    assert response.json() == {"password": True, "google": True}


async def test_not_configured(client: httpx.AsyncClient) -> None:
    response = await client.get("/auth/google/login")

    assert response.headers["location"] == login_page_with("google_not_configured")


# --- Accounts ----------------------------------------------------------------------------


async def test_first_sign_in_creates_a_verified_account(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    response = await sign_in(client, fake_google)

    assert response.status_code == 302
    assert response.headers["location"] == f"{settings.frontend_url}/auth/callback"
    assert refresh_cookie(response)  # signed in; no token in the URL
    user = await signed_in_user(client)
    assert user["email"] == "ada@example.com"
    assert user["full_name"] == "Ada Lovelace"
    assert user["avatar_url"] == "https://example.com/ada.png"
    assert user["email_verified"] is True
    assert user["has_password"] is False
    assert user["google_linked"] is True
    # Our app identified itself and proved it started this sign-in (PKCE).
    [form] = fake_google.token_requests
    assert form["code"] == ["one-time-code"]
    assert form["client_secret"] == ["test-secret"]
    assert form["grant_type"] == ["authorization_code"]


async def test_returning_user_is_found_by_google_id_not_email(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    await sign_in(client, fake_google)
    first_visit = await signed_in_user(client)

    await sign_in(client, fake_google, email="ada.renamed@example.com")

    second_visit = await signed_in_user(client)
    assert second_visit["id"] == first_visit["id"]
    assert second_visit["email"] == "ada@example.com"


async def test_links_google_to_a_verified_password_account(
    client: httpx.AsyncClient, fake_google: FakeGoogle, db: AsyncSession
) -> None:
    existing = await make_user(db, email="ada@example.com")
    existing.password_hash = await hash_password(PASSWORD)
    existing.email_verified_at = datetime.now(UTC)
    await db.flush()

    await sign_in(client, fake_google)

    user = await signed_in_user(client)
    assert user["id"] == existing.id
    assert user["google_linked"] is True
    assert user["has_password"] is True
    assert (await login(client)).status_code == 200  # the password still works


async def test_unproven_password_is_dropped_when_google_proves_the_email(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    # Someone registered ada@example.com but never proved they own that inbox. It
    # might be an attacker waiting for Ada to link Google ("account pre-hijacking").
    squatter = await register(client)

    await sign_in(client, fake_google)

    user = await signed_in_user(client)
    assert user["id"] == squatter.json()["user"]["id"]
    assert user["email_verified"] is True
    assert user["has_password"] is False
    assert (await login(client)).status_code == 401
    assert (await refresh(client, refresh_cookie(squatter))).status_code == 401


async def test_email_already_linked_to_another_google_account(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    await sign_in(client, fake_google, sub="google-id-1")

    response = await sign_in(client, fake_google, sub="google-id-2")

    assert response.headers["location"] == login_page_with("account_conflict")


async def test_disabled_account(
    client: httpx.AsyncClient, fake_google: FakeGoogle, db: AsyncSession
) -> None:
    await sign_in(client, fake_google)
    await db.execute(update(User).values(is_active=False))

    response = await sign_in(client, fake_google)

    assert response.headers["location"] == login_page_with("account_disabled")


async def test_google_must_have_verified_the_email(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    response = await sign_in(client, fake_google, email_verified=False)

    assert response.headers["location"] == login_page_with("email_not_verified")


# --- Attacks and failures ----------------------------------------------------------------


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-elses-app"},
        {"iss": "https://evil.example"},
        {"nonce": "from-another-sign-in"},
        {"exp": int(time.time()) - 60},
    ],
    ids=["other-apps-token", "wrong-issuer", "replayed-token", "expired"],
)
async def test_bad_id_tokens_are_refused(
    client: httpx.AsyncClient, fake_google: FakeGoogle, claims: dict[str, Any]
) -> None:
    response = await sign_in(client, fake_google, **claims)

    assert response.headers["location"] == login_page_with("google_failed")


async def test_id_token_not_signed_by_google_is_refused(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    fake_google.signing_key = ATTACKER_KEY

    response = await sign_in(client, fake_google)

    assert response.headers["location"] == login_page_with("google_failed")


async def test_failed_code_exchange(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    fake_google.token_status = 400

    response = await sign_in(client, fake_google)

    assert response.headers["location"] == login_page_with("google_failed")


async def test_state_must_match_the_cookie(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    await start(client, fake_google)

    response = await client.get(
        "/auth/google/callback", params={"code": "attackers-code", "state": "forged"}
    )

    assert response.headers["location"] == login_page_with("invalid_state")
    assert fake_google.token_requests == []  # never even asked Google


async def test_callback_from_another_browser_is_refused(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    params = await start(client, fake_google)
    client.cookies.clear()  # the state cookie lives in the browser that started

    response = await client.get(
        "/auth/google/callback",
        params={"code": "one-time-code", "state": params["state"]},
    )

    assert response.headers["location"] == login_page_with("invalid_state")


async def test_state_cookie_works_once(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    params = await start(client, fake_google)
    callback = {"code": "one-time-code", "state": params["state"]}

    first = await client.get("/auth/google/callback", params=callback)
    second = await client.get("/auth/google/callback", params=callback)

    assert first.headers["location"].endswith("/auth/callback")
    assert any(
        cookie.startswith(f"{google.STATE_COOKIE}=") and "Max-Age=0" in cookie
        for cookie in first.headers.get_list("set-cookie")
    )
    assert second.headers["location"] == login_page_with("invalid_state")


async def test_cancelled_at_google(
    client: httpx.AsyncClient, fake_google: FakeGoogle
) -> None:
    params = await start(client, fake_google)

    response = await client.get(
        "/auth/google/callback",
        params={"error": "access_denied", "state": params["state"]},
    )

    assert response.headers["location"] == login_page_with("google_access_denied")
