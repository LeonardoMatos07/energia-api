from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONDOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
FLAGS = "('Verde', 'Amarela', 'Vermelha P1', 'Vermelha P2', 'Escassez Hídrica')"
FORECAST_FLAGS = "('Verde', 'Amarela', 'Vermelha P1', 'Vermelha P2')"


def upgrade() -> None:
    op.create_table(
        "flag_rule_month",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("pld_min_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("pld_max_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("gh_mwmed", sa.Numeric(12, 3), nullable=False),
        sa.Column("gf_mwmed", sa.Numeric(12, 3), nullable=False),
        sa.Column("gsf", sa.Numeric(4, 2), nullable=False),
        sa.Column("pld_trigger_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("gfom_mwmed", sa.Numeric(12, 3), server_default=sa.text("0"), nullable=False),
        sa.Column("green_upper_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("yellow_upper_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("red1_upper_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("flag", sa.Text(), nullable=False),
        sa.Column("bulletin_number", sa.Integer(), nullable=True),
        sa.Column("bulletin_url", sa.Text(), nullable=True),
        sa.Column("bulletin_published_on", sa.Date(), nullable=True),
        sa.Column("revised_on", sa.Date(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "pld_min_brl_per_mwh <= pld_max_brl_per_mwh", name="ck_flag_rule_month_pld_range"
        ),
        sa.CheckConstraint(
            "green_upper_brl_per_mwh <= yellow_upper_brl_per_mwh "
            "AND yellow_upper_brl_per_mwh <= red1_upper_brl_per_mwh "
            "AND red1_upper_brl_per_mwh <= pld_max_brl_per_mwh",
            name="ck_flag_rule_month_band_order",
        ),
        sa.CheckConstraint(f"flag IN {FLAGS}", name="ck_flag_rule_month_flag"),
        sa.PrimaryKeyConstraint("month", name="pk_flag_rule_month"),
    )
    op.create_table(
        "flag_forecast_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("target_month", sa.Date(), nullable=False),
        sa.Column("lead_days", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("trigger_p10_brl_per_mwh", sa.Numeric(10, 2), nullable=True),
        sa.Column("trigger_p50_brl_per_mwh", sa.Numeric(10, 2), nullable=True),
        sa.Column("trigger_p90_brl_per_mwh", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("status IN ('loading', 'ready')", name="ck_flag_forecast_run_status"),
        sa.CheckConstraint("lead_days > 0", name="ck_flag_forecast_run_lead_days"),
        sa.PrimaryKeyConstraint("id", name="pk_flag_forecast_run"),
        sa.UniqueConstraint(
            "target_month", "lead_days", "generated_at", name="uq_flag_forecast_run"
        ),
    )
    op.create_index(
        "ix_flag_forecast_run_target", "flag_forecast_run", ["target_month", "lead_days"]
    )
    op.create_table(
        "flag_forecast_prob",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("flag", sa.Text(), nullable=False),
        sa.Column("prob", sa.Numeric(5, 4), nullable=False),
        sa.Column("band_lower_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.Column("band_upper_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.CheckConstraint(f"flag IN {FORECAST_FLAGS}", name="ck_flag_forecast_prob_flag"),
        sa.CheckConstraint("prob BETWEEN 0 AND 1", name="ck_flag_forecast_prob_prob_range"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["flag_forecast_run.id"],
            name="fk_flag_forecast_prob_run_id_flag_forecast_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "flag", name="pk_flag_forecast_prob"),
    )
    op.create_table(
        "flag_forecast_input",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("inputs", JSONDOC, nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["flag_forecast_run.id"],
            name="fk_flag_forecast_input_run_id_flag_forecast_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_flag_forecast_input"),
    )
    op.create_table(
        "flag_forecast_score",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("actual_flag", sa.Text(), nullable=False),
        sa.Column("prob_actual", sa.Numeric(5, 4), nullable=False),
        sa.Column("brier", sa.Numeric(8, 6), nullable=False),
        sa.Column("top_hit", sa.Boolean(), nullable=False),
        sa.Column(
            "scored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            f"actual_flag IN {FORECAST_FLAGS}", name="ck_flag_forecast_score_actual_flag"
        ),
        sa.CheckConstraint(
            "prob_actual BETWEEN 0 AND 1", name="ck_flag_forecast_score_prob_actual_range"
        ),
        sa.CheckConstraint("brier >= 0", name="ck_flag_forecast_score_brier"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["flag_forecast_run.id"],
            name="fk_flag_forecast_score_run_id_flag_forecast_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_flag_forecast_score"),
    )


def downgrade() -> None:
    op.drop_table("flag_forecast_score")
    op.drop_table("flag_forecast_input")
    op.drop_table("flag_forecast_prob")
    op.drop_index("ix_flag_forecast_run_target", table_name="flag_forecast_run")
    op.drop_table("flag_forecast_run")
    op.drop_table("flag_rule_month")
