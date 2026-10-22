from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hourly_curtailment",
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("day_type", sa.Text(), nullable=False),
        sa.Column("hour", sa.SmallInteger(), nullable=False),
        sa.Column("curtailed_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("generation_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name=op.f("ck_hourly_curtailment_hour")),
        sa.CheckConstraint(
            "day_type IN ('weekday', 'weekend')", name=op.f("ck_hourly_curtailment_day_type")
        ),
        sa.CheckConstraint(
            "source IN ('wind', 'solar', 'total')", name=op.f("ck_hourly_curtailment_source")
        ),
        sa.CheckConstraint(
            "subsystem IN ('N', 'NE', 'S', 'SE', 'SIN')",
            name=op.f("ck_hourly_curtailment_subsystem"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1", name=op.f("ck_hourly_curtailment_month_first_day")
        ),
        sa.PrimaryKeyConstraint(
            "subsystem", "source", "month", "day_type", "hour", name=op.f("pk_hourly_curtailment")
        ),
    )


def downgrade() -> None:
    op.drop_table("hourly_curtailment")
