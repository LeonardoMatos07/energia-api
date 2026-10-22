import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
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
        logger.warning("e-mail to %s | %s\n%s", message.to, message.subject, message.text)


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


SMTP_SSL_PORT = 465
SMTP_TIMEOUT_SECONDS = 15


class SmtpEmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        starttls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._starttls = starttls

    def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")
        context = ssl.create_default_context()
        if self._port == SMTP_SSL_PORT:
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=SMTP_TIMEOUT_SECONDS, context=context
            ) as smtp:
                self._deliver(smtp, mime)
            return
        with smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if self._starttls:
                smtp.starttls(context=context)
            self._deliver(smtp, mime)

    def _deliver(self, smtp: smtplib.SMTP, mime: MimeMessage) -> None:
        if self._username:
            smtp.login(self._username, self._password)
        smtp.send_message(mime)


def make_email_sender(settings: Settings) -> EmailSender:
    backend = settings.EMAIL_BACKEND or ("ses" if settings.ENVIRONMENT == "prod" else "console")
    if backend == "ses":
        return SesEmailSender(settings.EMAIL_SENDER, settings.SES_REGION)
    if backend == "smtp":
        return SmtpEmailSender(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            settings.SMTP_USERNAME,
            settings.SMTP_PASSWORD,
            settings.EMAIL_SENDER,
            settings.SMTP_STARTTLS,
        )
    return ConsoleEmailSender()


def send_safely(sender: EmailSender, message: EmailMessage) -> None:
    try:
        sender.send(message)
    except Exception:
        logger.exception("failed to send e-mail to %s", message.to)
