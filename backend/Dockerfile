# The backend image. The API, the Celery worker and database migrations all run from
# this one image; only the command differs (see compose.yaml).
#
#   docker build -t learnhub-backend backend/

# --- Stage 1: install the dependencies -----------------------------------------------
# This stage has uv, the tool that installs packages. The final image doesn't need it.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv
WORKDIR /app

# Compile packages to bytecode now, so containers start faster. Copy files instead of
# linking them, because the virtualenv is moved into another image below.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Only the dependency list, not our code: Docker caches this slow step and redoes it
# only when pyproject.toml or uv.lock change, not on every code change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# --- Stage 2: the image that runs ----------------------------------------------------
FROM python:3.13-slim

# Run as an ordinary user, not root, so a break-in can't take over the container.
RUN useradd --create-home learnhub
WORKDIR /app

COPY --from=build /app/.venv ./.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Use the virtualenv's python, uvicorn and celery, and print logs immediately.
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER learnhub

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
