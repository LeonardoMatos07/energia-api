from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPE = "scope IN ('national', 'international')"


def upgrade() -> None:
    op.create_table(
        "interchange_hourly",
        sa.Column("hour", sa.DateTime(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("hour_utc", sa.DateTime(), nullable=False),
        sa.Column("flow_mw_avg", sa.Numeric(12, 3), nullable=True),
        sa.Column("scheduled_mw_avg", sa.Numeric(12, 3), nullable=True),
        sa.CheckConstraint(SCOPE, name=op.f("ck_interchange_hourly_scope")),
        sa.PrimaryKeyConstraint(
            "hour", "origin", "destination", name=op.f("pk_interchange_hourly")
        ),
    )
    op.create_index("ix_interchange_hourly_hour", "interchange_hourly", ["hour"])

    op.create_table(
        "interchange_monthly",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("energy_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("scheduled_energy_mwh", sa.Numeric(16, 3), nullable=True),
        sa.Column("avg_mw", sa.Numeric(12, 3), nullable=False),
        sa.Column("peak_mw", sa.Numeric(12, 3), nullable=False),
        sa.Column("min_mw", sa.Numeric(12, 3), nullable=False),
        sa.CheckConstraint(SCOPE, name=op.f("ck_interchange_monthly_scope")),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1", name=op.f("ck_interchange_monthly_month_first_day")
        ),
        sa.PrimaryKeyConstraint(
            "month", "origin", "destination", name=op.f("pk_interchange_monthly")
        ),
    )

    op.create_table(
        "thermal_dispatch_monthly",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("plant_code", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("plant_name", sa.Text(), nullable=False),
        sa.Column("ceg", sa.Text(), nullable=True),
        sa.Column("fuel_type", sa.Text(), nullable=True),
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("verified_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("programmed_mwh", sa.Numeric(16, 3), nullable=True),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1",
            name=op.f("ck_thermal_dispatch_monthly_month_first_day"),
        ),
        sa.PrimaryKeyConstraint(
            "month", "plant_code", "reason", name=op.f("pk_thermal_dispatch_monthly")
        ),
    )
    op.create_index(
        "ix_thermal_dispatch_monthly_reason", "thermal_dispatch_monthly", ["reason", "month"]
    )


def downgrade() -> None:
    op.drop_index("ix_thermal_dispatch_monthly_reason", table_name="thermal_dispatch_monthly")
    op.drop_table("thermal_dispatch_monthly")
    op.drop_table("interchange_monthly")
    op.drop_index("ix_interchange_hourly_hour", table_name="interchange_hourly")
    op.drop_table("interchange_hourly")
