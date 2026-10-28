from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONDOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "forecast_model",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("mlflow_run_id", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_period", JSONDOC, nullable=False),
        sa.Column("n_series", sa.Integer(), nullable=False),
        sa.Column("n_rows", sa.Integer(), nullable=False),
        sa.Column("metrics", JSONDOC, nullable=False),
        sa.Column("calibration", JSONDOC, nullable=False),
        sa.Column("feature_importance", JSONDOC, nullable=False),
        sa.Column("model_card", JSONDOC, nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_model"),
        sa.UniqueConstraint("mlflow_run_id", name="uq_forecast_model_mlflow_run_id"),
    )
    op.create_index(
        "uq_forecast_model_current",
        "forecast_model",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current"),
    )
    op.create_table(
        "forecast_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("origin_ts", sa.DateTime(), nullable=False),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("horizon_hours", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("status IN ('loading', 'ready')", name="ck_forecast_run_status"),
        sa.ForeignKeyConstraint(
            ["model_id"], ["forecast_model.id"], name="fk_forecast_run_model_id_forecast_model"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_run"),
        sa.UniqueConstraint("model_id", "origin_ts", name="uq_forecast_run_model_origin"),
    )
    op.create_table(
        "forecast_hourly",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("hour_ts", sa.DateTime(), nullable=False),
        sa.Column("risk_probability", sa.Numeric(5, 4), nullable=False),
        sa.Column("expected_curtailment_pct", sa.Numeric(6, 2), nullable=False),
        sa.Column("applicable", sa.Boolean(), nullable=False),
        sa.CheckConstraint("source IN ('wind', 'solar')", name="ck_forecast_hourly_source"),
        sa.CheckConstraint(
            "risk_probability BETWEEN 0 AND 1", name="ck_forecast_hourly_risk_range"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["forecast_run.id"],
            name="fk_forecast_hourly_run_id_forecast_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "source", "scope", "hour_ts", name="pk_forecast_hourly"),
    )


def downgrade() -> None:
    op.drop_table("forecast_hourly")
    op.drop_table("forecast_run")
    op.drop_index("uq_forecast_model_current", table_name="forecast_model")
    op.drop_table("forecast_model")
