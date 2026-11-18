import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def google_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests start with Google sign-in off, whatever a local .env says. The Google tests
    switch it on with fake credentials."""
    monkeypatch.setattr(settings, "google_client_id", None)
    monkeypatch.setattr(settings, "google_client_secret", None)
