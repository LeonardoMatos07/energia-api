"""What a person gets right after turning the monthly newsletter on.

Two e-mails: a welcome that says what to expect, and the most recent report, so there is
something to read at once instead of waiting for the next month's data. The report is built
exactly as the monthly send builds it (same message, same idempotency key), so someone who
subscribes before that send is not mailed the same report twice.
"""

import hashlib
import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.dispatch import Recipient, build_messages, content_blocks, latest_report
from app.email import EmailMessage, EmailSender, send_safely, template_message
from app.email_templates import month_label

logger = logging.getLogger(__name__)


def _welcome_key(email: str) -> str:
    return f"energia-welcome-{hashlib.sha256(email.encode()).hexdigest()[:24]}"


def welcome_messages(
    session: Session, settings: Settings, recipient: Recipient
) -> list[EmailMessage]:
    """The welcome, then the latest published report when there is one. Built while the request
    still has its database session; sending happens afterwards."""
    report = latest_report(session)
    if report is None:
        latest = "O primeiro relatório chega assim que os próximos dados do ONS forem publicados."
    else:
        label = month_label(report.month.year, report.month.month)
        latest = (
            f"Em poucos minutos você recebe o relatório mais recente, o de {label}. "
            "Os próximos chegam todo mês, assim que os dados do ONS forem atualizados."
        )
    messages = [
        template_message(
            settings,
            "newsletter-welcome",
            recipient.email,
            {"name": recipient.name, "unsubscribe_url": recipient.unsubscribe_url},
            fixed={"latest_line": latest, "reports_url": f"{settings.web_url}/relatorios"},
            idempotency_key=_welcome_key(recipient.email),
        )
    ]
    if report is not None:
        blocks = content_blocks(session, settings, "report", report.month, report)
        messages += build_messages(settings, "report", report.month, [recipient], report, blocks)
    return messages


def send_all(sender: EmailSender, messages: list[EmailMessage]) -> None:
    for message in messages:
        send_safely(sender, message)
