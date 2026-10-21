import logging
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.auth import AdminDep
from app.cache import TTLCache
from app.config import Settings
from app.db import SessionDep
from app.models import LoadRun, MonthlyReport
from app.schemas import (
    AdminReport,
    AdminReportListResponse,
    LoadRunListResponse,
    ReportInput,
    RevalidateInput,
    RevalidateResponse,
    RevalidateTag,
)
from app.schemas import LoadRun as LoadRunSchema

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
        return False, "REVALIDATE_SECRET is not set; energia-web was not called."
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
        logger.warning("failed to revalidate energia-web (%s): %s", url, exc)
        return False, f"failed to call energia-web: {type(exc).__name__}"
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
    return RevalidateResponse(
        tags=tags,
        cache_cleared=True,
        entries_removed=removed,
        web_revalidated=reached,
        detail=detail,
    )
