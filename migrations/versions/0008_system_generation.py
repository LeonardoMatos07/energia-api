from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mw(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Numeric(12, 3), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "subsystem_hourly_balance",
        sa.Column("hour", sa.DateTime(), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("hour_utc", sa.DateTime(), nullable=False),
        _mw("hydro_mw_avg"),
        _mw("thermal_mw_avg"),
        _mw("wind_mw_avg"),
        _mw("solar_mw_avg"),
        _mw("load_mw_avg"),
        _mw("net_interchange_mw_avg"),
        sa.CheckConstraint(
            "subsystem IN ('N', 'NE', 'S', 'SE')",
            name=op.f("ck_subsystem_hourly_balance_subsystem"),
        ),
        sa.PrimaryKeyConstraint("hour", "subsystem", name=op.f("pk_subsystem_hourly_balance")),
    )
    op.create_index("ix_subsystem_hourly_balance_hour", "subsystem_hourly_balance", ["hour"])

    op.create_table(
        "subsystem_monthly_generation",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("energy_mwh", sa.Numeric(16, 3), nullable=False),
        _mw("avg_mw", nullable=False),
        _mw("peak_mw", nullable=False),
        sa.Column("share_pct", sa.Numeric(6, 2), nullable=True),
        sa.CheckConstraint(
            "source IN ('hydro', 'thermal', 'wind', 'solar')",
            name=op.f("ck_subsystem_monthly_generation_source"),
        ),
        sa.CheckConstraint(
            "subsystem IN ('N', 'NE', 'S', 'SE', 'SIN')",
            name=op.f("ck_subsystem_monthly_generation_subsystem"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1",
            name=op.f("ck_subsystem_monthly_generation_month_first_day"),
        ),
        sa.PrimaryKeyConstraint(
            "month", "subsystem", "source", name=op.f("pk_subsystem_monthly_generation")
        ),
    )

    op.create_table(
        "power_plant",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("fuel_type", sa.Text(), nullable=True),
        sa.Column("state", sa.CHAR(2), nullable=True),
        sa.Column("subsystem", sa.String(3), nullable=True),
        sa.Column("ceg", sa.Text(), nullable=True),
        sa.Column("capacity_mw", sa.Numeric(10, 3), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_type IN ('hydro', 'thermal', 'nuclear', 'wind', 'solar', 'other')",
            name=op.f("ck_power_plant_source_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_power_plant")),
        sa.UniqueConstraint("slug", name=op.f("uq_power_plant_slug")),
    )

    op.create_table(
        "plant_monthly_generation",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("plant_id", sa.Text(), nullable=False),
        sa.Column("energy_mwh", sa.Numeric(16, 3), nullable=False),
        _mw("avg_mw", nullable=False),
        _mw("peak_mw", nullable=False),
        sa.Column("capacity_factor", sa.Numeric(6, 4), nullable=True),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1",
            name=op.f("ck_plant_monthly_generation_month_first_day"),
        ),
        sa.CheckConstraint(
            "capacity_factor IS NULL OR capacity_factor BETWEEN 0 AND 1.05",
            name=op.f("ck_plant_monthly_generation_capacity_factor"),
        ),
        sa.ForeignKeyConstraint(
            ["plant_id"],
            ["power_plant.id"],
            name=op.f("fk_plant_monthly_generation_plant_id_power_plant"),
        ),
        sa.PrimaryKeyConstraint("month", "plant_id", name=op.f("pk_plant_monthly_generation")),
    )


def downgrade() -> None:
    op.drop_table("plant_monthly_generation")
    op.drop_table("power_plant")
    op.drop_table("subsystem_monthly_generation")
    op.drop_index("ix_subsystem_hourly_balance_hour", table_name="subsystem_hourly_balance")
    op.drop_table("subsystem_hourly_balance")
