import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_MONTH_RE = re.compile(MONTH_PATTERN)

Source = Literal["wind", "solar"]
AggregateSource = Literal["wind", "solar", "total"]
Kind = Literal["plant", "cluster"]
SubsystemOrSin = Literal["N", "NE", "S", "SE", "SIN"]
RevalidateTag = Literal["curtailment", "plants", "reports"]
ALL_TAGS: list[RevalidateTag] = ["curtailment", "plants", "reports"]


def parse_month(value: Any) -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.replace(day=1)
    if isinstance(value, str) and _MONTH_RE.match(value):
        return date(int(value[:4]), int(value[5:7]), 1)
    raise ValueError("month must use the YYYY-MM format")


def format_month(value: date) -> str:
    return value.strftime("%Y-%m")


Month = Annotated[
    date,
    BeforeValidator(parse_month),
    PlainSerializer(format_month, return_type=str),
    WithJsonSchema({"type": "string", "pattern": MONTH_PATTERN, "examples": ["2026-09"]}),
]


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _CurtailmentAggregate(_Orm):
    source: AggregateSource
    month: Month
    verified_generation_mwh: float
    curtailed_mwh: float
    curtailment_pct: float | None
    curtailed_rel_mwh: float
    curtailed_cnf_mwh: float
    curtailed_ene_mwh: float
    curtailed_other_mwh: float
    estimated_loss_brl: float | None
    affected_plants: int
    updated_at: datetime


class SubsystemCurtailment(_CurtailmentAggregate):
    subsystem: str


class StateCurtailment(_CurtailmentAggregate):
    state: str


class PlantCurtailment(_Orm):
    plant_id: str
    month: Month
    verified_generation_mwh: float
    curtailed_mwh: float
    curtailment_pct: float | None
    curtailed_rel_mwh: float
    curtailed_cnf_mwh: float
    curtailed_ene_mwh: float
    curtailed_other_mwh: float
    estimated_loss_brl: float | None
    price_basis: str | None
    restricted_hours: float
    state_avg_curtailment_pct: float | None
    national_rank: int | None
    state_rank: int | None
    updated_at: datetime


class PlantSummary(_Orm):
    id: str
    kind: Kind
    name: str
    slug: str
    source: Source
    state: str
    subsystem: str
    capacity_mw: float | None


class Plant(PlantSummary):
    cluster_id: str | None
    ceg: str | None
    connection_point: str | None
    operator: str | None
    updated_at: datetime


class RankedPlant(PlantSummary):
    rank: int
    curtailed_mwh: float
    curtailment_pct: float | None
    estimated_loss_brl: float | None


class SubsystemCurtailmentResponse(BaseModel):
    items: list[SubsystemCurtailment]


class StateCurtailmentResponse(BaseModel):
    series: list[StateCurtailment]
    ranking: list[RankedPlant]
    ranking_month: Month | None


class CurtailmentSummary(BaseModel):
    scope: str
    source: AggregateSource
    start: Month | None
    end: Month | None
    months: int
    verified_generation_mwh: float
    curtailed_mwh: float
    curtailment_pct: float | None
    estimated_loss_brl: float | None
    affected_plants: int


class PlantListResponse(BaseModel):
    items: list[PlantSummary]
    total: int
    page: int
    page_size: int


class PlantDetailResponse(BaseModel):
    plant: Plant
    cluster: PlantSummary | None
    cluster_plants: list[PlantSummary]
    series: list[PlantCurtailment]


class RankingResponse(BaseModel):
    month: Month | None
    items: list[RankedPlant]


class MonthsResponse(BaseModel):
    months: list[Month]


class ReportSummary(_Orm):
    month: Month
    title: str
    slug: str
    summary: str | None
    published_at: datetime | None


class Report(ReportSummary):
    content: str


class AdminReport(Report):
    created_at: datetime
    updated_at: datetime


class ReportListResponse(BaseModel):
    items: list[ReportSummary]


class AdminReportListResponse(BaseModel):
    items: list[AdminReport]


class ReportInput(BaseModel):
    month: Month
    title: str = Field(min_length=1, max_length=300)
    slug: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=200,
        description="Optional; generated from the title when missing.",
    )
    summary: str | None = None
    content: str = Field(min_length=1)
    publish: bool


class NewsletterSignup(BaseModel):
    email: EmailStr = Field(max_length=254)
    consent: Literal[True]
    source: str | None = Field(default=None, max_length=100)


class MessageResponse(BaseModel):
    message: str


class ConfirmedResponse(BaseModel):
    confirmed: bool


class UnsubscribedResponse(BaseModel):
    unsubscribed: bool


class LoadRun(_Orm):
    id: int
    dataset: str
    period: Month | None
    source_file: str | None
    checksum: str | None
    row_count: int | None
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "success", "failed"]
    detail: str | None


class LoadRunListResponse(BaseModel):
    items: list[LoadRun]


class RevalidateInput(BaseModel):
    tags: list[RevalidateTag] = Field(default_factory=lambda: list(ALL_TAGS))


class RevalidateResponse(BaseModel):
    tags: list[RevalidateTag]
    cache_cleared: bool
    entries_removed: int
    web_revalidated: bool
    detail: str | None


class HealthResponse(BaseModel):
    status: str
    database: str
