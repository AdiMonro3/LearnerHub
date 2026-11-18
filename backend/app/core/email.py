"""Sending email.

The API never talks to the mail server itself. send_email() puts a job on the queue
(RabbitMQ) and returns at once; the Celery worker picks the job up and sends the email
(deliver_email). So a slow or broken mail server can't slow down or break a request:
the worker just tries again a little later.

In dev the mail server is Mailpit (compose.yaml): read every email at
http://localhost:8025. Nothing leaves your machine.
"""

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.worker import celery_app


async def send_email(to: str, subject: str, body: str) -> None:
    """Runs in the API: queue an email for the worker to send."""
    # .delay() is an ordinary blocking network call (it talks to RabbitMQ). to_thread
    # runs it on a helper thread, so the server keeps answering other requests meanwhile.
    await asyncio.to_thread(deliver_email.delay, to, subject, body)


@celery_app.task(
    # If the mail server is down or says no, try again, waiting a little longer each
    # time (up to 1s, 2s, 4s, 8s, 16s), then give up. smtplib's errors are OSErrors.
    autoretry_for=(OSError,),
    retry_backoff=True,
    max_retries=5,
    # Nobody waits for an answer, so don't store one in Redis.
    ignore_result=True,
)
def deliver_email(to: str, subject: str, body: str) -> None:
    """Runs in the worker: build the email and hand it to the mail server."""
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(message)
