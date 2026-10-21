from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import MonthlyReport
from app.schemas import Report, ReportListResponse, ReportSummary

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("", response_model=ReportListResponse)
def list_reports(session: SessionDep, cache: PublicCacheDep) -> ReportListResponse:
    def load() -> ReportListResponse:
        rows = session.scalars(
            select(MonthlyReport)
            .where(MonthlyReport.published_at.is_not(None))
            .order_by(MonthlyReport.month.desc(), MonthlyReport.published_at.desc())
        ).all()
        return ReportListResponse(items=[ReportSummary.model_validate(r) for r in rows])

    return cache.get_or_set(load)


@router.get("/{slug}", response_model=Report)
def get_report(slug: str, session: SessionDep, cache: PublicCacheDep) -> Report:
    def load() -> Report | None:
        row = session.scalars(
            select(MonthlyReport).where(
                MonthlyReport.slug == slug, MonthlyReport.published_at.is_not(None)
            )
        ).first()
        return Report.model_validate(row) if row else None

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return result
