import logging
import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from app.auth import AdminDep
from app.cache import TTLCache
from app.config import Settings
from app.db import SessionDep
from app.events import log_event
from app.models import LoadRun, Member, MonthlyReport, UsageEvent
from app.schemas import (
    AdminReport,
    AdminReportListResponse,
    Lead,
    LeadListResponse,
    LeadTier,
    LoadRunListResponse,
    MemberTimeline,
    PurgeResponse,
    ReportInput,
    RevalidateInput,
    RevalidateResponse,
    RevalidateTag,
    UsageEventList,
    UsageEventOut,
    UsageSummary,
)
from app.schemas import LoadRun as LoadRunSchema
from app.usage import event_filters, events_csv, purge_events, usage_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "report"


@router.get("/load-runs", response_model=LoadRunListResponse)
def list_load_runs(
    _admin: AdminDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> LoadRunListResponse:
    rows = session.scalars(
        select(LoadRun).order_by(LoadRun.started_at.desc(), LoadRun.id.desc()).limit(limit)
    ).all()
    return LoadRunListResponse(items=[LoadRunSchema.model_validate(r) for r in rows])


@router.get("/reports", response_model=AdminReportListResponse)
def list_all_reports(_admin: AdminDep, session: SessionDep) -> AdminReportListResponse:
    rows = session.scalars(
        select(MonthlyReport).order_by(MonthlyReport.month.desc(), MonthlyReport.id.desc())
    ).all()
    return AdminReportListResponse(items=[AdminReport.model_validate(r) for r in rows])


@router.post("/reports", response_model=AdminReport)
def upsert_report(
    body: ReportInput, admin: AdminDep, session: SessionDep, request: Request
) -> AdminReport:
    slug = body.slug or slugify(body.title)
    now = datetime.now(UTC)
    row = session.scalars(select(MonthlyReport).where(MonthlyReport.slug == slug)).first()
    if row is None:
        row = MonthlyReport(slug=slug, created_at=now)
        session.add(row)
    row.month = body.month
    row.title = body.title
    row.summary = body.summary
    row.content = body.content
    row.updated_at = now
    if body.publish:
        row.published_at = row.published_at or now
    else:
        row.published_at = None
    log_event(
        session,
        request,
        category="admin",
        action="admin.report_saved",
        properties={
            "slug": slug,
            "month": body.month.isoformat(),
            "published": body.publish,
            "actor": admin.subject,
            "actor_kind": admin.kind,
        },
    )
    session.commit()
    session.refresh(row)
    cache: TTLCache = request.app.state.cache
    cache.clear()
    logger.info("report %s saved by %s (%s)", slug, admin.subject, admin.kind)
    return AdminReport.model_validate(row)


def notify_web(
    settings: Settings, tags: list[RevalidateTag], transport: httpx.BaseTransport | None = None
) -> tuple[bool, str | None]:
    if not settings.REVALIDATE_SECRET:
        return False, "REVALIDATE_SECRET is not set; energy-web was not called."
    url = f"{settings.web_url}/api/revalidate"
    try:
        with httpx.Client(timeout=settings.REVALIDATE_TIMEOUT_SECONDS, transport=transport) as c:
            response = c.post(
                url,
                json={"tags": tags},
                headers={"x-revalidate-secret": settings.REVALIDATE_SECRET},
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("failed to revalidate energy-web (%s): %s", url, exc)
        return False, f"failed to call energy-web: {type(exc).__name__}"
    return True, None


@router.post("/revalidate", response_model=RevalidateResponse)
def revalidate(
    admin: AdminDep, request: Request, body: RevalidateInput | None = None
) -> RevalidateResponse:
    tags = (body or RevalidateInput()).tags
    cache: TTLCache = request.app.state.cache
    removed = cache.clear()
    settings: Settings = request.app.state.settings
    reached, detail = notify_web(settings, tags, request.app.state.web_transport)
    logger.info("revalidation %s by %s: web=%s", tags, admin.subject, reached)
    with request.app.state.sessionmaker() as audit:
        log_event(
            audit,
            request,
            category="admin",
            action="admin.revalidate",
            properties={"tags": list(tags), "actor": admin.subject, "web_reached": reached},
        )
        audit.commit()
    return RevalidateResponse(
        tags=tags,
        cache_cleared=True,
        entries_removed=removed,
        web_revalidated=reached,
        detail=detail,
    )


@router.get("/leads", response_model=LeadListResponse)
def list_leads(
    _admin: AdminDep,
    session: SessionDep,
    tier: Annotated[LeadTier | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> LeadListResponse:
    stmt = select(Member)
    if tier is not None:
        stmt = stmt.where(Member.lead_tier == tier)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Member.lead_score.desc(), Member.created_at.desc()).limit(limit)
    ).all()
    return LeadListResponse(items=[Lead.model_validate(row) for row in rows], total=total)


DEFAULT_USAGE_DAYS = 30
MAX_USAGE_DAYS = 730
MAX_CSV_ROWS = 200_000


@router.get("/usage/summary", response_model=UsageSummary)
def usage_overview(
    _admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=MAX_USAGE_DAYS)] = DEFAULT_USAGE_DAYS,
    include_bots: bool = False,
) -> UsageSummary:
    return usage_summary(session, days, include_bots)


@router.get("/usage/events", response_model=UsageEventList)
def usage_events(
    _admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=MAX_USAGE_DAYS)] = DEFAULT_USAGE_DAYS,
    action: Annotated[str | None, Query(max_length=80)] = None,
    section: Annotated[str | None, Query(max_length=40)] = None,
    member_id: uuid.UUID | None = None,
    include_bots: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UsageEventList:
    since = datetime.now(UTC) - timedelta(days=days)
    conditions = event_filters(since, action, section, member_id, include_bots)
    total = session.scalar(select(func.count()).select_from(UsageEvent).where(*conditions)) or 0
    rows = session.scalars(
        select(UsageEvent)
        .where(*conditions)
        .order_by(UsageEvent.occurred_at.desc(), UsageEvent.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return UsageEventList(items=[UsageEventOut.model_validate(row) for row in rows], total=total)


@router.get("/usage/events.csv")
def usage_events_csv(
    _admin: AdminDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=MAX_USAGE_DAYS)] = DEFAULT_USAGE_DAYS,
    limit: Annotated[int, Query(ge=1, le=MAX_CSV_ROWS)] = 50_000,
) -> Response:
    since = datetime.now(UTC) - timedelta(days=days)
    return Response(
        content=events_csv(session, since, limit),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="usage-events.csv"'},
    )


@router.get("/usage/members/{member_id}", response_model=MemberTimeline)
def member_timeline(
    member_id: uuid.UUID,
    _admin: AdminDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> MemberTimeline:
    member = session.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    conditions = [UsageEvent.member_id == member_id]
    total = session.scalar(select(func.count()).select_from(UsageEvent).where(*conditions)) or 0
    rows = session.scalars(
        select(UsageEvent)
        .where(*conditions)
        .order_by(UsageEvent.occurred_at.desc(), UsageEvent.id.desc())
        .limit(limit)
    ).all()
    return MemberTimeline(
        member=Lead.model_validate(member),
        events=[UsageEventOut.model_validate(row) for row in rows],
        total=total,
    )


@router.post("/usage/purge", response_model=PurgeResponse)
def usage_purge(
    admin: AdminDep,
    request: Request,
    session: SessionDep,
    days: Annotated[int | None, Query(ge=30, le=3650)] = None,
) -> PurgeResponse:
    settings: Settings = request.app.state.settings
    keep = days or settings.EVENT_RETENTION_DAYS
    deleted = purge_events(session, keep)
    logger.info("usage events older than %s days purged (%s) by %s", keep, deleted, admin.subject)
    return PurgeResponse(deleted=deleted, older_than_days=keep)
