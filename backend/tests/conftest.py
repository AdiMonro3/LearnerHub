"""Shared test fixtures.

The tests run against a real, throwaway Postgres in Docker (Testcontainers), built by
running the same Alembic migrations as production. Constraints, cascades and generated
columns are database behaviour, so testing them against SQLite or mocks would prove
nothing.
"""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from app.core import email
from app.core.db import get_session
from app.main import app
from tests.helpers import Email

BACKEND_DIR = Path(__file__).resolve().parent.parent
POSTGRES_IMAGE = "pgvector/pgvector:pg17"


def _point_testcontainers_at_docker() -> None:
    """Testcontainers only checks DOCKER_HOST and /var/run/docker.sock. OrbStack, Colima
    and Docker Desktop publish their socket through the Docker CLI's current context
    instead, so ask the CLI where the daemon is."""
    if os.environ.get("DOCKER_HOST"):
        return
    try:
        result = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return  # no Docker CLI: let Testcontainers try its defaults and report the error
    if host := result.stdout.strip():
        os.environ["DOCKER_HOST"] = host


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Start Postgres once per test run and migrate it to the latest schema."""
    _point_testcontainers_at_docker()
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        # No alembic.ini here, so Alembic doesn't reconfigure pytest's logging.
        config = Config()
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One connection per test, inside a transaction that is rolled back afterwards.

    Every test starts from empty tables and nothing it writes leaks into the next test.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


def _session_on(connection: AsyncConnection) -> AsyncSession:
    # join_transaction_mode="create_savepoint": commit() and rollback() only touch a
    # SAVEPOINT inside the test's transaction, never the transaction itself.
    return AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )


@pytest.fixture
async def db(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """The test's own session, for setting up rows and checking results."""
    async with _session_on(connection) as session:
        yield session


@pytest.fixture
async def client(connection: AsyncConnection) -> AsyncIterator[httpx.AsyncClient]:
    """Calls the app in-process, like the browser would.

    Each request gets its own session, as in production, so anything a request forgets
    to commit is thrown away when it ends. All of them share the test's connection, so
    the test sees what requests committed (use `db.refresh(obj)` for rows it loaded
    earlier) and everything is still rolled back at the end.
    """

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with _session_on(connection) as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    transport = httpx.ASGITransport(app=app)
    # Same host the real API uses, so cookie handling matches the browser's.
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8000"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[Email]:
    """In every test, emails are caught here instead of being queued for the worker,
    so no test ever needs RabbitMQ or a mail server. Ask for `outbox` by name to see
    what was sent."""
    sent: list[Email] = []

    def catch(to: str, subject: str, body: str) -> None:
        sent.append(Email(to, subject, body))

    # Replaces the call that would put the job on the RabbitMQ queue.
    monkeypatch.setattr(email.deliver_email, "delay", catch)
    return sent
