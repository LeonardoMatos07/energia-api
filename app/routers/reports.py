from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.exporting.pdf import report_pdf
from app.models import MonthlyReport
from app.routers.exports import PDF_MEDIA_TYPE, download, throttle
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


@router.get("/{slug}/pdf", summary="A published report as a PDF")
def get_report_pdf(slug: str, request: Request, session: SessionDep) -> Response:
    throttle(request)
    row = session.scalars(
        select(MonthlyReport).where(
            MonthlyReport.slug == slug, MonthlyReport.published_at.is_not(None)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    pdf = report_pdf(row.title, row.month, row.summary, row.content)
    return download(pdf, PDF_MEDIA_TYPE, f"spillway-relatorio-{row.slug}.pdf")
