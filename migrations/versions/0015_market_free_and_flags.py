from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tariff_flag_monthly",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("flag", sa.Text(), nullable=False),
        sa.Column("additional_brl_per_mwh", sa.Numeric(10, 2), nullable=False),
        sa.PrimaryKeyConstraint("month", name=op.f("pk_tariff_flag_monthly")),
        sa.CheckConstraint(
            "additional_brl_per_mwh >= 0", name=op.f("ck_tariff_flag_monthly_additional")
        ),
    )
    op.create_table(
        "market_consumption_monthly",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("regulated_mw_avg", sa.Numeric(14, 3), nullable=False),
        sa.Column("free_mw_avg", sa.Numeric(14, 3), nullable=False),
        sa.PrimaryKeyConstraint("month", name=op.f("pk_market_consumption_monthly")),
        sa.CheckConstraint(
            "regulated_mw_avg >= 0 AND free_mw_avg >= 0",
            name=op.f("ck_market_consumption_monthly_positive"),
        ),
    )
    op.create_table(
        "market_agents_monthly",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("agent_class", sa.Text(), nullable=False),
        sa.Column("agents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("month", "agent_class", name=op.f("pk_market_agents_monthly")),
        sa.CheckConstraint("agents >= 0", name=op.f("ck_market_agents_monthly_agents")),
    )
    op.execute(
        sa.text(
            "INSERT INTO data_source (slug, publisher_name, dataset_name, dataset_url, "
            "license_code, license_url, license_verified_at, attribution_text, update_frequency) "
            "VALUES "
            "('aneel-tariff-flags', 'Agência Nacional de Energia Elétrica (ANEEL)', "
            "'Bandeiras tarifárias', "
            "'https://dadosabertos.aneel.gov.br/dataset/bandeiras-tarifarias', 'ODbL-1.0', "
            "'https://opendatacommons.org/licenses/odbl/1-0/', '2026-10-30', "
            "'Fonte: ANEEL, Bandeiras tarifárias, licença ODbL 1.0.', 'Mensal'), "
            "('ccee-market', 'Câmara de Comercialização de Energia Elétrica (CCEE)', "
            "'Consumo por ambiente de comercialização e agentes por classe', "
            "'https://dadosabertos.ccee.org.br/dataset/consumo_mensal_ambiente_comercializacao', "
            "'CC-BY-4.0', 'https://creativecommons.org/licenses/by/4.0/deed.pt-br', "
            "'2026-10-30', "
            "'Fonte: CCEE, consumo por ambiente de comercialização e agentes por classe, "
            "licença CC BY 4.0.', 'Mensal')"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM data_source WHERE slug IN ('aneel-tariff-flags', 'ccee-market')")
    )
    for table in ("market_agents_monthly", "market_consumption_monthly", "tariff_flag_monthly"):
        op.drop_table(table)
