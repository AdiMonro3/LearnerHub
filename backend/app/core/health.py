"""Health checks, for Docker, load balancers and you.

GET /health        "Is the app running?" Touches nothing else, so a database outage
                   doesn't make Docker restart an app that is itself fine.
GET /health/ready  "Can the app do its job right now?" Checks the database, Redis and
                   RabbitMQ. 503 if any of them is down, saying which.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import settings
from app.core.db import DbSession
from app.worker import celery_app

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])

# A check that takes longer than this counts as down: a hung service is no use either.
CHECK_TIMEOUT_SECONDS = 3


@router.get("")
async def alive() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: DbSession) -> JSONResponse:
    async def check_database() -> None:
        await db.execute(text("SELECT 1"))

    # Run the three checks at the same time, so the slowest one sets the total time.
    results = await asyncio.gather(
        _run_check("database", check_database),
        _run_check("redis", check_redis),
        _run_check("rabbitmq", check_rabbitmq),
    )
    checks = dict(results)  # e.g. {"database": "ok", "redis": "unavailable", ...}
    all_ok = all(status == "ok" for status in checks.values())
    return JSONResponse(
        {"status": "ok" if all_ok else "unavailable", "checks": checks},
        status_code=200 if all_ok else 503,
    )


async def _run_check(
    name: str, check: Callable[[], Awaitable[None]]
) -> tuple[str, str]:
    """Run one check: "ok" if it finishes in time without an error, else "unavailable"."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await check()
    except Exception as exc:  # noqa: BLE001 - deliberate: any failure means "not ready"
        logger.warning("Health check failed: %s: %r", name, exc)
        return name, "unavailable"
    return name, "ok"


async def check_redis() -> None:
    client = Redis.from_url(settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def check_rabbitmq() -> None:
    def connect() -> None:
        # Opens (and closes) a real connection, logging in like the app does.
        with celery_app.connection_for_write() as connection:
            connection.connect()

    # Connecting is blocking network code, so it runs on a helper thread.
    await asyncio.to_thread(connect)
