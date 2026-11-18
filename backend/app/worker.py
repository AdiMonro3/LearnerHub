"""The Celery worker: runs slow jobs in the background, outside the web request.

How it fits together:
1. The API calls `some_task.delay(...)`. That puts a job message in RabbitMQ and
   returns at once, so the user doesn't wait.
2. The worker, a separate program, takes jobs from RabbitMQ and runs them.
3. A job's return value is stored in Redis, so whoever started it can check the result.

Start the worker (from backend/, with `docker compose up -d` running):

    uv run celery -A app.worker worker --loglevel=info
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "learnhub",
    broker=settings.rabbitmq_url,  # where jobs wait: RabbitMQ
    backend=settings.redis_url,  # where results are kept: Redis
    # Modules that define jobs. The worker imports them at startup to learn their jobs.
    include=["app.core.email"],
)


@celery_app.task
def ping() -> str:
    """A job that does nothing but answer. It proves the whole path works:
    API -> RabbitMQ -> worker -> Redis -> back to the caller."""
    return "pong"
