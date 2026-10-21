import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailSender:
    def send(self, message: EmailMessage) -> None:
        logger.info("e-mail to %s | %s\n%s", message.to, message.subject, message.text)


class SesEmailSender:
    def __init__(self, sender: str, region: str, client: Any | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("sesv2", region_name=region)
        self._client = client
        self._sender = sender

    def send(self, message: EmailMessage) -> None:
        self._client.send_email(
            FromEmailAddress=self._sender,
            Destination={"ToAddresses": [message.to]},
            Content={
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": message.text, "Charset": "UTF-8"},
                        "Html": {"Data": message.html, "Charset": "UTF-8"},
                    },
                }
            },
        )


def make_email_sender(settings: Settings) -> EmailSender:
    if settings.ENVIRONMENT == "prod":
        return SesEmailSender(settings.EMAIL_SENDER, settings.SES_REGION)
    return ConsoleEmailSender()


def send_safely(sender: EmailSender, message: EmailMessage) -> None:
    try:
        sender.send(message)
    except Exception:
        logger.exception("failed to send e-mail to %s", message.to)
