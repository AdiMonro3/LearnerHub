"""Health checks. The database check runs for real (the test database); Redis and
RabbitMQ aren't running in tests, so their checks are replaced with stand-ins."""

import asyncio

import httpx
import pytest

from app.core import health


async def answers() -> None:
    return None


async def refuses() -> None:
    raise ConnectionRefusedError("connection refused")


async def hangs() -> None:
    await asyncio.sleep(10)


async def test_alive_answers_without_checking_anything(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_redis", refuses)

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_everything_answers(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_redis", answers)
    monkeypatch.setattr(health, "check_rabbitmq", answers)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "rabbitmq": "ok"},
    }


async def test_not_ready_says_which_service_is_down(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_redis", answers)
    monkeypatch.setattr(health, "check_rabbitmq", refuses)

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "ok", "redis": "ok", "rabbitmq": "unavailable"},
    }


async def test_a_check_that_hangs_counts_as_down(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(health, "check_redis", hangs)
    monkeypatch.setattr(health, "check_rabbitmq", answers)

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"
