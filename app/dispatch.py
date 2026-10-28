"""The monthly send: the report to subscribers and members, the analyses to members.

Run from the admin endpoint (`POST /v1/admin/dispatches`) or from a scheduler:

    python -m app.dispatch report            # the latest published report
    python -m app.dispatch analysis --month 2026-09

Each recipient gets its own message with a fixed idempotency key (kind, month, address), so a
run that stops halfway can be repeated: Relay answers the ones already accepted with the original
message instead of sending a second e-mail.
"""

import argparse
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.email import EmailMessage, EmailSender, make_email_sender, template_message
from app.email_templates import month_label
from app.models import EmailDispatch, Member, MonthlyReport, NewsletterSubscriber
from app.schemas import parse_month

logger = logging.getLogger(__name__)

Kind = Literal["report", "analysis"]
DEFAULT_NAME = "assinante"
SUMMARY_FALLBACK = "Já está no ar o relatório mensal do Spillway sobre o curtailment de energia."
PROGRESS_EVERY = 200


@dataclass(frozen=True)
class Recipient:
    email: str
    name: str
    unsubscribe_url: str


def unsubscribe_token(settings: Settings, subscriber_id: int) -> str:
    """A link that unsubscribes one subscriber, valid for as long as the secret is.

    Signed rather than stored: the database keeps only hashes of the tokens it hands out, so a
    token that must be in every monthly e-mail cannot be one of those.
    """
    secret = (settings.EVENT_HASH_SECRET or "local-development-secret").encode()
    signature = hmac.new(secret, f"unsubscribe:{subscriber_id}".encode(), hashlib.sha256)
    return f"{subscriber_id}.{signature.hexdigest()}"


def verify_unsubscribe_token(settings: Settings, token: str) -> int | None:
    head, _, _ = token.partition(".")
    if not head.isdigit():
        return None
    expected = unsubscribe_token(settings, int(head))
    return int(head) if hmac.compare_digest(expected, token) else None


def _first_name(name: str) -> str:
    parts = name.split()
    return parts[0] if parts else DEFAULT_NAME


def recipients_for(session: Session, settings: Settings, kind: Kind) -> list[Recipient]:
    """Verified members who accepted marketing e-mail, plus (for the report) confirmed
    subscribers. Someone in both lists is mailed once, by their member name."""
    manage_url = f"{settings.web_url}/conta"
    found: dict[str, Recipient] = {}

    members = session.scalars(
        select(Member).where(Member.email_verified_at.is_not(None), Member.marketing_opt_in)
    ).all()
    for member in members:
        found[member.email] = Recipient(member.email, _first_name(member.name), manage_url)

    if kind == "report":
        subscribers = session.scalars(
            select(NewsletterSubscriber).where(
                NewsletterSubscriber.confirmed_at.is_not(None),
                NewsletterSubscriber.unsubscribed_at.is_(None),
            )
        ).all()
        for subscriber in subscribers:
            link = (
                f"{settings.web_url}/newsletter/descadastrar"
                f"?token={unsubscribe_token(settings, subscriber.id)}"
            )
            known = found.get(subscriber.email)
            found[subscriber.email] = Recipient(
                subscriber.email, known.name if known else DEFAULT_NAME, link
            )
    return sorted(found.values(), key=lambda r: r.email)


def _key(kind: Kind, month: date, email: str) -> str:
    digest = hashlib.sha256(email.encode()).hexdigest()[:24]
    return f"energia-{kind}-{month:%Y-%m}-{digest}"


def build_messages(
    settings: Settings,
    kind: Kind,
    month: date,
    recipients: list[Recipient],
    report: MonthlyReport | None,
) -> list[EmailMessage]:
    label = month_label(month.year, month.month)
    messages: list[EmailMessage] = []
    for recipient in recipients:
        variables: dict[str, str | int] = {"name": recipient.name}
        fixed: dict[str, str | int] = {"month_label": label}
        if kind == "report":
            if report is None:
                raise DispatchError("a report dispatch needs its report")
            variables["unsubscribe_url"] = recipient.unsubscribe_url
            fixed |= {
                "report_title": report.title,
                "report_summary": report.summary or SUMMARY_FALLBACK,
                "report_url": f"{settings.web_url}/relatorios/{report.slug}",
            }
        messages.append(
            template_message(
                settings,
                kind,
                recipient.email,
                variables,
                fixed=fixed,
                idempotency_key=_key(kind, month, recipient.email),
            )
        )
    return messages


def latest_report(session: Session, month: date | None = None) -> MonthlyReport | None:
    stmt = (
        select(MonthlyReport)
        .where(MonthlyReport.published_at.is_not(None))
        .order_by(MonthlyReport.month.desc(), MonthlyReport.published_at.desc())
    )
    if month is not None:
        stmt = stmt.where(MonthlyReport.month == month)
    return session.scalars(stmt).first()


class DispatchError(Exception):
    """The send cannot start; the message says why."""


def open_dispatch(
    session: Session, kind: Kind, month: date, *, retry: bool = False
) -> EmailDispatch:
    """Reserves the (kind, month) send. An existing one is refused unless it did not finish and
    `retry` is set: repeating is safe because every message has a fixed idempotency key."""
    existing = session.scalars(
        select(EmailDispatch).where(EmailDispatch.kind == kind, EmailDispatch.month == month)
    ).first()
    if existing is not None:
        if existing.status == "partial" and retry:
            existing.status = "running"
            existing.finished_at = None
            session.commit()
            return existing
        raise DispatchError(f"the {kind} for {month:%Y-%m} was already sent ({existing.status})")
    report = latest_report(session, month) if kind == "report" else None
    if kind == "report" and report is None:
        raise DispatchError(f"there is no published report for {month:%Y-%m}")
    row = EmailDispatch(kind=kind, month=month, report_id=report.id if report else None)
    session.add(row)
    session.commit()
    return row


def run_dispatch(
    factory: sessionmaker[Session],
    sender: EmailSender,
    settings: Settings,
    dispatch_id: int,
) -> None:
    with factory() as session:
        row = session.get(EmailDispatch, dispatch_id)
        if row is None:
            return
        kind: Kind = "report" if row.kind == "report" else "analysis"
        report = session.get(MonthlyReport, row.report_id) if row.report_id else None
        recipients = recipients_for(session, settings, kind)
        messages = build_messages(settings, kind, row.month, recipients, report)
        row.recipients = len(messages)
        row.sent = row.failed = 0
        session.commit()

        for number, message in enumerate(messages, start=1):
            try:
                sender.send(message)
                row.sent += 1
            except Exception:
                row.failed += 1
                logger.exception("dispatch %s: could not send to recipient %s", row.id, number)
            if number % PROGRESS_EVERY == 0:
                session.commit()

        row.status = "done" if row.failed == 0 else "partial"
        row.finished_at = datetime.now(UTC)
        session.commit()
        logger.info(
            "dispatch %s (%s %s): %s sent, %s failed",
            row.id,
            row.kind,
            f"{row.month:%Y-%m}",
            row.sent,
            row.failed,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the monthly report or analyses e-mail.")
    parser.add_argument("kind", choices=["report", "analysis"])
    parser.add_argument("--month", help="YYYY-MM; defaults to the latest published report")
    parser.add_argument("--retry", action="store_true", help="finish a send that stopped partway")
    args = parser.parse_args(argv)

    settings = get_settings()
    factory = make_sessionmaker(make_engine(settings.database_url))
    kind: Kind = "report" if args.kind == "report" else "analysis"
    with factory() as session:
        month = parse_month(args.month) if args.month else None
        if month is None:
            latest = latest_report(session)
            if latest is None:
                print("no published report to send")
                return 1
            month = latest.month
        try:
            dispatch_id = open_dispatch(session, kind, month, retry=args.retry).id
        except DispatchError as error:
            print(error)
            return 1
    run_dispatch(factory, make_email_sender(settings), settings, dispatch_id)
    with factory() as session:
        done = session.get(EmailDispatch, dispatch_id)
        if done is None:
            return 1
        print(f"{done.kind} {done.month:%Y-%m}: {done.sent} sent, {done.failed} failed")
        return 0 if done.failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
