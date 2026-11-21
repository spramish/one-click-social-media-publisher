"""Transactional email delivery.

The API never depends on a concrete transport. :func:`send_email` dispatches to
whichever :class:`EmailBackend` ``EMAIL_BACKEND`` selects:

* ``console`` — writes the message (including the action link) to the log. This
  is the development default so the registration and password-reset flows can be
  exercised without an SMTP server. It **refuses to run** when
  ``ENVIRONMENT=production`` so a verification link can never leak into
  production logs.
* ``smtp`` — real delivery over STARTTLS using the standard library, so no
  extra dependency is required.
"""

from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage as StdlibEmailMessage
from email.utils import formataddr
from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A single outbound message."""

    to: str
    subject: str
    text_body: str
    html_body: str | None = None


class EmailBackend(ABC):
    """Transport contract."""

    @abstractmethod
    def send(self, message: EmailMessage) -> None:
        """Deliver ``message`` or raise."""


class ConsoleEmailBackend(EmailBackend):
    """Logs messages instead of sending them (development only)."""

    def send(self, message: EmailMessage) -> None:
        if settings.environment == "production":
            raise RuntimeError(
                "EMAIL_BACKEND=console is not permitted in production — "
                "verification links must not be written to logs. Use EMAIL_BACKEND=smtp."
            )
        logger.info(
            "email (console backend)",
            extra={
                "to": message.to,
                "subject": message.subject,
                "body": message.text_body,
            },
        )


class SMTPEmailBackend(EmailBackend):
    """Delivers messages through an SMTP relay."""

    def send(self, message: EmailMessage) -> None:
        outgoing = StdlibEmailMessage()
        outgoing["From"] = formataddr((settings.email_from_name, settings.email_from_address))
        outgoing["To"] = message.to
        outgoing["Subject"] = message.subject
        outgoing.set_content(message.text_body)
        if message.html_body:
            outgoing.add_alternative(message.html_body, subtype="html")

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_starttls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(outgoing)


@lru_cache
def get_email_backend() -> EmailBackend:
    """Return the configured backend (cached for the process lifetime)."""
    if settings.email_backend == "smtp":
        return SMTPEmailBackend()
    return ConsoleEmailBackend()


def reset_email_backend_cache() -> None:
    """Drop the cached backend (used by tests after changing settings)."""
    get_email_backend.cache_clear()


def send_email(message: EmailMessage) -> None:
    """Deliver ``message`` through the configured backend."""
    get_email_backend().send(message)
