"""Sending email: the API side queues a job, the worker side sends it."""

from email.message import EmailMessage
from typing import Self

import pytest

from app.core import email
from app.core.config import settings
from tests.helpers import Email


async def test_send_email_queues_a_job_for_the_worker(outbox: list[Email]) -> None:
    await email.send_email("ada@example.com", "Hello", "Body")

    assert outbox == [Email("ada@example.com", "Hello", "Body")]


def test_deliver_email_hands_the_message_to_the_mail_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers: list[tuple[str, int]] = []
    sent: list[EmailMessage] = []

    class FakeSMTP:
        """Stands in for smtplib.SMTP: keeps each message instead of sending it."""

        def __init__(self, host: str, port: int, timeout: float) -> None:
            servers.append((host, port))

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *exc_info: object) -> None:
            pass

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)

    # Calling a Celery task directly runs it right here, as the worker would.
    email.deliver_email("ada@example.com", "Hello", "Line one\nLine two")

    assert servers == [(settings.smtp_host, settings.smtp_port)]
    [message] = sent
    assert message["From"] == settings.mail_from
    assert message["To"] == "ada@example.com"
    assert message["Subject"] == "Hello"
    assert message.get_content() == "Line one\nLine two\n"
