import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import SessionDep
from app.events import EventContext, build_event, classify_path, context_from_request, trusted_web
from app.models import UsageEvent
from app.routers.auth import member_from_token
from app.schemas import EventBatch, EventsAccepted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", tags=["events"])

DUPLICATE_WINDOW = timedelta(seconds=2)


def is_duplicate_view(
    session: Session,
    context: EventContext,
    action: str,
    path: str | None,
    properties: dict[str, object],
) -> bool:
    if context.session_key is None:
        return False
    recent = session.scalars(
        select(UsageEvent.properties).where(
            UsageEvent.category == "page",
            UsageEvent.session_key == context.session_key,
            UsageEvent.action == action,
            UsageEvent.path == path,
            UsageEvent.occurred_at >= datetime.now(UTC) - DUPLICATE_WINDOW,
        )
    ).all()
    return any(row == properties for row in recent)


def store_events(factory: sessionmaker[Session], context: EventContext, batch: EventBatch) -> None:
    try:
        with factory() as session:
            for item in batch.events:
                classification = classify_path(item.path or "", item.query)
                properties = {**classification.properties, **item.properties}
                if item.category == "page" and is_duplicate_view(
                    session, context, item.action, item.path, properties
                ):
                    continue
                session.add(
                    build_event(
                        context,
                        category=item.category,
                        action=item.action,
                        section=item.section or classification.section,
                        entity_type=item.entity_type or classification.entity_type,
                        entity_id=item.entity_id or classification.entity_id,
                        path=item.path,
                        properties=properties,
                    )
                )
            session.commit()
    except Exception:
        logger.exception("failed to store %d usage events", len(batch.events))


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventsAccepted,
    responses={401: {"description": "Missing or wrong web secret"}},
)
def ingest(
    batch: EventBatch,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> EventsAccepted:
    settings: Settings = request.app.state.settings
    if settings.WEB_API_SECRET:
        if not trusted_web(request, settings):
            raise HTTPException(status_code=401, detail="Invalid web secret.")
    elif settings.ENVIRONMENT == "prod":
        raise HTTPException(status_code=503, detail="Event ingestion is not configured.")

    member = member_from_token(session, authorization)
    base = context_from_request(request, settings, member)
    context = EventContext(
        member=member,
        visitor_id=batch.visitor_id or base.visitor_id,
        session_key=batch.session_key or base.session_key,
        ip_hash=base.ip_hash,
        user_agent=batch.user_agent or base.user_agent,
        referrer=batch.referrer or base.referrer,
        request_id=batch.request_id or str(uuid.uuid4()),
    )
    background.add_task(store_events, request.app.state.sessionmaker, context, batch)
    return EventsAccepted(accepted=len(batch.events))
