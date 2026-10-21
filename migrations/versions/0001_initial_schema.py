from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _month_check(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "EXTRACT(DAY FROM month) = 1", name=op.f(f"ck_{table}_month_first_day")
    )


def _aggregate_columns() -> list[sa.Column[sa.Numeric]]:
    return [
        sa.Column("verified_generation_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("curtailed_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("curtailment_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("curtailed_rel_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("curtailed_cnf_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("curtailed_ene_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("curtailed_other_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("estimated_loss_brl", sa.Numeric(16, 2), nullable=True),
        sa.Column("affected_plants", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "plant",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("state", sa.CHAR(2), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("cluster_id", sa.Text(), nullable=True),
        sa.Column("ceg", sa.Text(), nullable=True),
        sa.Column("capacity_mw", sa.Numeric(10, 3), nullable=True),
        sa.Column("connection_point", sa.Text(), nullable=True),
        sa.Column("operator", sa.Text(), nullable=True),
        _ts("updated_at"),
        sa.CheckConstraint("kind IN ('plant', 'cluster')", name=op.f("ck_plant_kind")),
        sa.CheckConstraint("source IN ('wind', 'solar')", name=op.f("ck_plant_source")),
        sa.CheckConstraint("subsystem IN ('N', 'NE', 'S', 'SE')", name=op.f("ck_plant_subsystem")),
        sa.CheckConstraint(
            "length(state) = 2 AND state = upper(state)", name=op.f("ck_plant_state")
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["plant.id"], name=op.f("fk_plant_cluster_id_plant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plant")),
        sa.UniqueConstraint("slug", name=op.f("uq_plant_slug")),
    )
    op.create_index("ix_plant_state_source", "plant", ["state", "source"])
    op.create_index("ix_plant_cluster_id", "plant", ["cluster_id"])
    op.create_index("ix_plant_name_lower", "plant", [sa.text("lower(name)")])
    _create_trigram_index()

    op.create_table(
        "plant_monthly_curtailment",
        sa.Column("plant_id", sa.Text(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("verified_generation_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("curtailed_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("curtailment_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("curtailed_rel_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("curtailed_cnf_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("curtailed_ene_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("curtailed_other_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("estimated_loss_brl", sa.Numeric(16, 2), nullable=True),
        sa.Column("price_basis", sa.Text(), nullable=True),
        sa.Column("restricted_hours", sa.Numeric(7, 1), nullable=False),
        sa.Column("state_avg_curtailment_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("national_rank", sa.Integer(), nullable=True),
        sa.Column("state_rank", sa.Integer(), nullable=True),
        _ts("updated_at"),
        sa.CheckConstraint(
            "price_basis IS NULL OR price_basis IN ('PLD', 'CMO', 'PLD_FILLED', 'CMO_FILLED')",
            name=op.f("ck_plant_monthly_curtailment_price_basis"),
        ),
        _month_check("plant_monthly_curtailment"),
        sa.ForeignKeyConstraint(
            ["plant_id"],
            ["plant.id"],
            name=op.f("fk_plant_monthly_curtailment_plant_id_plant"),
        ),
        sa.PrimaryKeyConstraint("plant_id", "month", name=op.f("pk_plant_monthly_curtailment")),
    )
    op.create_index(
        "ix_plant_monthly_curtailment_month_national_rank",
        "plant_monthly_curtailment",
        ["month", "national_rank"],
    )
    op.create_index(
        "ix_plant_monthly_curtailment_month_state_rank",
        "plant_monthly_curtailment",
        ["month", "state_rank"],
    )

    op.create_table(
        "state_monthly_curtailment",
        sa.Column("state", sa.CHAR(2), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        *_aggregate_columns(),
        _ts("updated_at"),
        sa.CheckConstraint(
            "source IN ('wind', 'solar', 'total')",
            name=op.f("ck_state_monthly_curtailment_source"),
        ),
        sa.CheckConstraint(
            "length(state) = 2 AND state = upper(state)",
            name=op.f("ck_state_monthly_curtailment_state"),
        ),
        _month_check("state_monthly_curtailment"),
        sa.PrimaryKeyConstraint(
            "state", "source", "month", name=op.f("pk_state_monthly_curtailment")
        ),
    )

    op.create_table(
        "subsystem_monthly_curtailment",
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        *_aggregate_columns(),
        _ts("updated_at"),
        sa.CheckConstraint(
            "source IN ('wind', 'solar', 'total')",
            name=op.f("ck_subsystem_monthly_curtailment_source"),
        ),
        sa.CheckConstraint(
            "subsystem IN ('N', 'NE', 'S', 'SE', 'SIN')",
            name=op.f("ck_subsystem_monthly_curtailment_subsystem"),
        ),
        _month_check("subsystem_monthly_curtailment"),
        sa.PrimaryKeyConstraint(
            "subsystem", "source", "month", name=op.f("pk_subsystem_monthly_curtailment")
        ),
    )

    op.create_table(
        "load_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("checksum", sa.Text(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name=op.f("ck_load_run_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_load_run")),
    )
    op.create_index("ix_load_run_started_at", "load_run", [sa.text("started_at DESC")])

    op.create_table(
        "monthly_report",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _month_check("monthly_report"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monthly_report")),
        sa.UniqueConstraint("slug", name=op.f("uq_monthly_report_slug")),
    )

    op.create_table(
        "newsletter_subscriber",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmation_token_hash", sa.Text(), nullable=True),
        sa.Column("confirmation_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribe_token_hash", sa.Text(), nullable=False),
        sa.Column("signup_source", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(
            "email = lower(email)", name=op.f("ck_newsletter_subscriber_email_lowercase")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_newsletter_subscriber")),
        sa.UniqueConstraint("email", name=op.f("uq_newsletter_subscriber_email")),
    )
    op.create_index(
        "ix_newsletter_subscriber_confirmation_token_hash",
        "newsletter_subscriber",
        ["confirmation_token_hash"],
    )
    op.create_index(
        "ix_newsletter_subscriber_unsubscribe_token_hash",
        "newsletter_subscriber",
        ["unsubscribe_token_hash"],
    )

    op.create_table(
        "organization",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization")),
    )

    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cognito_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        _ts("created_at"),
        sa.CheckConstraint("role IN ('owner', 'member', 'admin')", name=op.f("ck_app_user_role")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_app_user_organization_id_organization"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("cognito_sub", name=op.f("uq_app_user_cognito_sub")),
        sa.UniqueConstraint("email", name=op.f("uq_app_user_email")),
    )


def _create_trigram_index() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
    ).scalar()
    if not available:
        return
    savepoint = bind.begin_nested()
    try:
        bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        savepoint.commit()
    except sa.exc.DBAPIError:
        savepoint.rollback()
        return
    op.create_index(
        "ix_plant_name_trgm",
        "plant",
        [sa.text("lower(name) gin_trgm_ops")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("app_user")
    op.drop_table("organization")
    op.drop_table("newsletter_subscriber")
    op.drop_table("monthly_report")
    op.drop_table("load_run")
    op.drop_table("subsystem_monthly_curtailment")
    op.drop_table("state_monthly_curtailment")
    op.drop_table("plant_monthly_curtailment")
    op.drop_table("plant")
