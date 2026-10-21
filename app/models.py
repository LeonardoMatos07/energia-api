import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    Uuid,
    extract,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

BigIntId = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _now_default() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Plant(Base):
    __tablename__ = "plant"
    __table_args__ = (
        CheckConstraint("kind IN ('plant', 'cluster')", name="kind"),
        CheckConstraint("source IN ('wind', 'solar')", name="source"),
        CheckConstraint("subsystem IN ('N', 'NE', 'S', 'SE')", name="subsystem"),
        CheckConstraint("length(state) = 2 AND state = upper(state)", name="state"),
        Index("ix_plant_state_source", "state", "source"),
        Index("ix_plant_cluster_id", "cluster_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    subsystem: Mapped[str] = mapped_column(String(3), nullable=False)
    cluster_id: Mapped[str | None] = mapped_column(Text, ForeignKey("plant.id"), nullable=True)
    ceg: Mapped[str | None] = mapped_column(Text)
    capacity_mw: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    connection_point: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now_default()


Index("ix_plant_name_lower", func.lower(Plant.name))


class PlantMonthlyCurtailment(Base):
    __tablename__ = "plant_monthly_curtailment"
    __table_args__ = (
        CheckConstraint(
            "price_basis IS NULL OR price_basis IN ('PLD', 'CMO', 'PLD_FILLED', 'CMO_FILLED')",
            name="price_basis",
        ),
        Index("ix_plant_monthly_curtailment_month_national_rank", "month", "national_rank"),
        Index("ix_plant_monthly_curtailment_month_state_rank", "month", "state_rank"),
    )

    plant_id: Mapped[str] = mapped_column(Text, ForeignKey("plant.id"), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    verified_generation_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    curtailed_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    curtailment_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    curtailed_rel_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    curtailed_cnf_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    curtailed_ene_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    curtailed_other_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    estimated_loss_brl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    price_basis: Mapped[str | None] = mapped_column(Text)
    restricted_hours: Mapped[Decimal] = mapped_column(Numeric(7, 1), nullable=False)
    state_avg_curtailment_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    national_rank: Mapped[int | None] = mapped_column(Integer)
    state_rank: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = _now_default()


class _CurtailmentAggregate:
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    verified_generation_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    curtailed_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    curtailment_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    curtailed_rel_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    curtailed_cnf_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    curtailed_ene_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    curtailed_other_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    estimated_loss_brl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    affected_plants: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = _now_default()


class StateMonthlyCurtailment(_CurtailmentAggregate, Base):
    __tablename__ = "state_monthly_curtailment"
    __table_args__ = (
        CheckConstraint("source IN ('wind', 'solar', 'total')", name="source"),
        CheckConstraint("length(state) = 2 AND state = upper(state)", name="state"),
    )

    state: Mapped[str] = mapped_column(CHAR(2), primary_key=True)


class SubsystemMonthlyCurtailment(_CurtailmentAggregate, Base):
    __tablename__ = "subsystem_monthly_curtailment"
    __table_args__ = (
        CheckConstraint("source IN ('wind', 'solar', 'total')", name="source"),
        CheckConstraint("subsystem IN ('N', 'NE', 'S', 'SE', 'SIN')", name="subsystem"),
    )

    subsystem: Mapped[str] = mapped_column(String(3), primary_key=True)


class LoadRun(Base):
    __tablename__ = "load_run"
    __table_args__ = (CheckConstraint("status IN ('running', 'success', 'failed')", name="status"),)

    id: Mapped[int] = mapped_column(BigIntId, Identity(), primary_key=True)
    dataset: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[date | None] = mapped_column(Date)
    source_file: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)


Index("ix_load_run_started_at", LoadRun.started_at.desc())


class MonthlyReport(Base):
    __tablename__ = "monthly_report"

    id: Mapped[int] = mapped_column(BigIntId, Identity(), primary_key=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now_default()
    updated_at: Mapped[datetime] = _now_default()


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscriber"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        Index("ix_newsletter_subscriber_confirmation_token_hash", "confirmation_token_hash"),
        Index("ix_newsletter_subscriber_unsubscribe_token_hash", "unsubscribe_token_hash"),
    )

    id: Mapped[int] = mapped_column(BigIntId, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_token_hash: Mapped[str | None] = mapped_column(Text)
    confirmation_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribe_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    signup_source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_default()


class Organization(Base):
    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now_default()


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member', 'admin')", name="role"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cognito_sub: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organization.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now_default()


for _name in (
    "plant_monthly_curtailment",
    "state_monthly_curtailment",
    "subsystem_monthly_curtailment",
    "monthly_report",
):
    _table = Base.metadata.tables[_name]
    _table.append_constraint(
        CheckConstraint(extract("day", _table.c.month) == 1, name="month_first_day")
    )
