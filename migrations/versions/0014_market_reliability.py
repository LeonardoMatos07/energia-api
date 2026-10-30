from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSYSTEMS = "subsystem IN ('N', 'NE', 'S', 'SE')"


def _price_stats() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("pld_avg_brl_per_mwh", sa.Numeric(12, 2), nullable=False),
        sa.Column("pld_min_brl_per_mwh", sa.Numeric(12, 2), nullable=False),
        sa.Column("pld_max_brl_per_mwh", sa.Numeric(12, 2), nullable=False),
        sa.Column("pld_std_brl_per_mwh", sa.Numeric(12, 2), nullable=True),
        sa.Column("hours_at_floor", sa.Integer(), nullable=False),
        sa.Column("hours_at_cap", sa.Integer(), nullable=False),
        sa.Column("hours", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "data_source",
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("publisher_name", sa.Text(), nullable=False),
        sa.Column("dataset_name", sa.Text(), nullable=False),
        sa.Column("dataset_url", sa.Text(), nullable=False),
        sa.Column("license_code", sa.Text(), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=False),
        sa.Column("license_verified_at", sa.Date(), nullable=False),
        sa.Column("attribution_text", sa.Text(), nullable=False),
        sa.Column("update_frequency", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_data_source")),
    )
    op.execute(
        sa.text(
            "INSERT INTO data_source (slug, publisher_name, dataset_name, dataset_url, "
            "license_code, license_url, license_verified_at, attribution_text, update_frequency) "
            "VALUES "
            "('ccee-pld', 'Câmara de Comercialização de Energia Elétrica (CCEE)', "
            "'Preço de Liquidação das Diferenças (PLD)', "
            "'https://dadosabertos.ccee.org.br/dataset/pld_horario', 'CC-BY-4.0', "
            "'https://creativecommons.org/licenses/by/4.0/deed.pt-br', '2026-10-30', "
            "'Fonte: CCEE, Preço de Liquidação das Diferenças (PLD), licença CC BY 4.0.', "
            "'Mensal'), "
            "('aneel-reliability', 'Agência Nacional de Energia Elétrica (ANEEL)', "
            "'Indicadores coletivos de continuidade (DEC e FEC)', "
            "'https://dadosabertos.aneel.gov.br/dataset/"
            "indicadores-coletivos-de-continuidade-dec-e-fec', 'ODbL-1.0', "
            "'https://opendatacommons.org/licenses/odbl/1-0/', '2026-10-30', "
            "'Fonte: ANEEL, Indicadores coletivos de continuidade (DEC e FEC), "
            "licença ODbL 1.0.', 'Mensal')"
        )
    )

    op.create_table(
        "pld_hourly",
        sa.Column("hour_utc", sa.DateTime(), nullable=False),
        sa.Column("hour", sa.DateTime(), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("variant", sa.String(10), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("pld_brl_per_mwh", sa.Numeric(12, 2), nullable=False),
        sa.PrimaryKeyConstraint(
            "hour_utc", "subsystem", "variant", "method", name=op.f("pk_pld_hourly")
        ),
        sa.CheckConstraint(SUBSYSTEMS, name=op.f("ck_pld_hourly_subsystem")),
        sa.CheckConstraint("variant IN ('official', 'shadow')", name=op.f("ck_pld_hourly_variant")),
        sa.CheckConstraint("pld_brl_per_mwh >= 0", name=op.f("ck_pld_hourly_price")),
    )
    op.create_index(op.f("ix_pld_hourly_hour"), "pld_hourly", ["hour"])

    op.create_table(
        "pld_weekly",
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("subsystem", sa.String(3), nullable=False),
        sa.Column("pld_avg_brl_per_mwh", sa.Numeric(12, 2), nullable=False),
        sa.PrimaryKeyConstraint("week_start", "subsystem", name=op.f("pk_pld_weekly")),
        sa.CheckConstraint(SUBSYSTEMS, name=op.f("ck_pld_weekly_subsystem")),
    )
    for table, key in (("pld_daily", "day"), ("pld_monthly", "month")):
        op.create_table(
            table,
            sa.Column(key, sa.Date(), nullable=False),
            sa.Column("subsystem", sa.String(3), nullable=False),
            *_price_stats(),
            sa.PrimaryKeyConstraint(key, "subsystem", name=op.f(f"pk_{table}")),
            sa.CheckConstraint(SUBSYSTEMS, name=op.f(f"ck_{table}_subsystem")),
        )

    op.create_table(
        "distributor",
        sa.Column("cnpj", sa.Text(), nullable=False),
        sa.Column("acronym", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("cnpj", name=op.f("pk_distributor")),
    )
    op.create_table(
        "consumer_unit_set",
        sa.Column("distributor_cnpj", sa.Text(), nullable=False),
        sa.Column("set_id", sa.Text(), nullable=False),
        sa.Column("set_name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("distributor_cnpj", "set_id", name=op.f("pk_consumer_unit_set")),
        sa.ForeignKeyConstraint(
            ["distributor_cnpj"],
            ["distributor.cnpj"],
            name=op.f("fk_consumer_unit_set_distributor_cnpj_distributor"),
        ),
    )
    op.create_table(
        "reliability_monthly",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("distributor_cnpj", sa.Text(), nullable=False),
        sa.Column("set_id", sa.Text(), nullable=False),
        sa.Column("consumers", sa.BigInteger(), nullable=True),
        sa.Column("dec_hours", sa.Numeric(12, 2), nullable=True),
        sa.Column("fec_count", sa.Numeric(12, 2), nullable=True),
        sa.Column("dec_limit_hours", sa.Numeric(12, 2), nullable=True),
        sa.Column("fec_limit_count", sa.Numeric(12, 2), nullable=True),
        sa.PrimaryKeyConstraint(
            "month", "distributor_cnpj", "set_id", name=op.f("pk_reliability_monthly")
        ),
        sa.ForeignKeyConstraint(
            ["distributor_cnpj", "set_id"],
            ["consumer_unit_set.distributor_cnpj", "consumer_unit_set.set_id"],
            name=op.f("fk_reliability_monthly_distributor_cnpj_consumer_unit_set"),
        ),
        sa.CheckConstraint(
            "dec_hours IS NULL OR dec_hours >= 0", name=op.f("ck_reliability_monthly_dec")
        ),
        sa.CheckConstraint(
            "fec_count IS NULL OR fec_count >= 0", name=op.f("ck_reliability_monthly_fec")
        ),
    )
    op.create_index(
        op.f("ix_reliability_monthly_distributor_cnpj"), "reliability_monthly", ["distributor_cnpj"]
    )


def downgrade() -> None:
    for table in (
        "reliability_monthly",
        "consumer_unit_set",
        "distributor",
        "pld_monthly",
        "pld_daily",
        "pld_weekly",
        "pld_hourly",
        "data_source",
    ):
        op.drop_table(table)
