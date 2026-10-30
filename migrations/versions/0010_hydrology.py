from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _num(name: str, precision: int, scale: int = 3) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Numeric(precision, scale), nullable=True)


def upgrade() -> None:
    op.create_table(
        "reservoir",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("reservoir_type", sa.Text(), nullable=True),
        sa.Column("planning_code", sa.Integer(), nullable=True),
        sa.Column("station_code", sa.Integer(), nullable=True),
        sa.Column("plant_name", sa.Text(), nullable=True),
        sa.Column("ceg", sa.Text(), nullable=True),
        sa.Column("subsystem", sa.String(3), nullable=True),
        sa.Column("ree", sa.Text(), nullable=True),
        sa.Column("basin", sa.Text(), nullable=True),
        sa.Column("river", sa.Text(), nullable=True),
        sa.Column("commissioned_on", sa.Date(), nullable=True),
        _num("max_level_m", 10),
        _num("min_level_m", 10),
        _num("max_volume_hm3", 14),
        _num("min_volume_hm3", 14),
        _num("useful_volume_hm3", 14),
        _num("latitude", 10, 6),
        _num("longitude", 10, 6),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservoir")),
        sa.UniqueConstraint("slug", name=op.f("uq_reservoir_slug")),
    )

    op.create_table(
        "subsystem_storage_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=False),
        _num("storage_max_mw_month", 14),
        _num("storage_mw_month", 14),
        _num("storage_pct", 7, 4),
        _num("ena_gross_mw_avg", 14),
        _num("ena_gross_pct_mlt", 10, 4),
        _num("ena_storable_mw_avg", 14),
        _num("ena_storable_pct_mlt", 10, 4),
        sa.CheckConstraint(
            "subsystem IN ('N', 'NE', 'S', 'SE')", name=op.f("ck_subsystem_storage_daily_subsystem")
        ),
        sa.CheckConstraint(
            "storage_pct IS NULL OR storage_pct BETWEEN 0 AND 100",
            name=op.f("ck_subsystem_storage_daily_storage_pct"),
        ),
        sa.PrimaryKeyConstraint("day", "subsystem", name=op.f("pk_subsystem_storage_daily")),
    )

    op.create_table(
        "reservoir_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reservoir_id", sa.Text(), nullable=False),
        _num("storage_mw_month", 14),
        _num("storage_max_mw_month", 14),
        _num("storage_pct", 7, 4),
        _num("useful_volume_pct", 8),
        _num("ena_gross_mw_avg", 14),
        _num("ena_gross_pct_mlt", 10, 4),
        _num("ena_storable_mw_avg", 14),
        _num("ena_storable_pct_mlt", 10, 4),
        _num("inflow_m3s", 12),
        _num("natural_inflow_m3s", 12),
        _num("outflow_m3s", 12),
        _num("turbined_m3s", 12),
        _num("spilled_m3s", 12),
        _num("upstream_level_m", 10),
        _num("downstream_level_m", 10),
        sa.CheckConstraint(
            "storage_pct IS NULL OR storage_pct BETWEEN 0 AND 200",
            name=op.f("ck_reservoir_daily_storage_pct"),
        ),
        sa.ForeignKeyConstraint(
            ["reservoir_id"],
            ["reservoir.id"],
            name=op.f("fk_reservoir_daily_reservoir_id_reservoir"),
        ),
        sa.PrimaryKeyConstraint("day", "reservoir_id", name=op.f("pk_reservoir_daily")),
    )
    op.create_index(
        "ix_reservoir_daily_reservoir_id_day", "reservoir_daily", ["reservoir_id", "day"]
    )

    op.create_table(
        "plant_monthly_spilled_energy",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("plant_code", sa.Text(), nullable=False),
        sa.Column("plant_name", sa.Text(), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=True),
        sa.Column("basin", sa.Text(), nullable=True),
        sa.Column("river", sa.Text(), nullable=True),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("spilled_energy_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("spilled_turbinable_energy_mwh", sa.Numeric(16, 3), nullable=False),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1",
            name=op.f("ck_plant_monthly_spilled_energy_month_first_day"),
        ),
        sa.PrimaryKeyConstraint(
            "month", "plant_code", name=op.f("pk_plant_monthly_spilled_energy")
        ),
    )


def downgrade() -> None:
    op.drop_table("plant_monthly_spilled_energy")
    op.drop_index("ix_reservoir_daily_reservoir_id_day", table_name="reservoir_daily")
    op.drop_table("reservoir_daily")
    op.drop_table("subsystem_storage_daily")
    op.drop_table("reservoir")
