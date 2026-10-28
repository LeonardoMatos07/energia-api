from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.config import Settings
from app.db import SessionDep
from app.exporting.data import Analysis, load_analysis, tables
from app.exporting.files import XLSX_MEDIA_TYPE, to_csv, to_xlsx
from app.exporting.pdf import analysis_pdf
from app.ratelimit import RateLimiter
from app.routers.newsletter import client_ip
from app.schemas import MONTH_PATTERN, AggregateSource, SubsystemOrSin, parse_month

router = APIRouter(prefix="/v1/exports", tags=["exports"])

MonthQuery = Annotated[str | None, Query(pattern=MONTH_PATTERN, examples=["2026-09"])]
Dataset = Literal["summary", "subsystems", "operators", "connection-points", "hourly"]
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
PDF_MEDIA_TYPE = "application/pdf"


def throttle(request: Request) -> None:
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.export_limiter
    if not limiter.allow(client_ip(request, settings)):
        raise HTTPException(status_code=429, detail="Too many downloads; try again in a minute.")


def download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _analysis(
    request: Request,
    session: SessionDep,
    scope: str,
    source: AggregateSource,
    start: str | None,
    end: str | None,
) -> Analysis:
    throttle(request)
    first = parse_month(start) if start is not None else None
    last = parse_month(end) if end is not None else None
    if first is not None and last is not None and first > last:
        raise HTTPException(status_code=422, detail="start must not be after end.")
    return load_analysis(session, scope, source, first, last)


@router.get("/analysis.pdf", summary="The analyses as a PDF report")
def analysis_pdf_file(
    request: Request,
    session: SessionDep,
    scope: SubsystemOrSin = "SIN",
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
) -> Response:
    data = _analysis(request, session, scope, source, start, end)
    return download(analysis_pdf(data), PDF_MEDIA_TYPE, f"{data.file_stem}.pdf")


@router.get("/analysis.xlsx", summary="Every analysis table in one Excel workbook")
def analysis_xlsx_file(
    request: Request,
    session: SessionDep,
    scope: SubsystemOrSin = "SIN",
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
) -> Response:
    data = _analysis(request, session, scope, source, start, end)
    return download(to_xlsx(data), XLSX_MEDIA_TYPE, f"{data.file_stem}.xlsx")


@router.get("/{dataset}.csv", summary="One analysis table as CSV")
def analysis_csv_file(
    request: Request,
    session: SessionDep,
    dataset: Dataset,
    scope: SubsystemOrSin = "SIN",
    source: AggregateSource = "total",
    start: MonthQuery = None,
    end: MonthQuery = None,
) -> Response:
    data = _analysis(request, session, scope, source, start, end)
    table = tables(data)[dataset]
    return download(to_csv(table), CSV_MEDIA_TYPE, f"{data.file_stem}-{dataset}.csv")
