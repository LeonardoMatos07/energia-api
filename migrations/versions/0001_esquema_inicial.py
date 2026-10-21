"""esquema inicial da Fase 1

Revision ID: 0001
Revises:
Create Date: 2026-10-20
"""

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


def _mes_check(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "EXTRACT(DAY FROM mes) = 1", name=op.f(f"ck_{table}_mes_primeiro_dia")
    )


def _aggregate_columns() -> list[sa.Column[sa.Numeric]]:
    return [
        sa.Column("geracao_verificada_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("energia_cortada_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("percentual_corte", sa.Numeric(6, 2), nullable=True),
        sa.Column("corte_rel_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("corte_cnf_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("corte_ene_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("corte_outros_mwh", sa.Numeric(16, 3), nullable=False),
        sa.Column("perda_estimada_brl", sa.Numeric(16, 2), nullable=True),
        sa.Column("usinas_afetadas", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "usina",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("uf", sa.CHAR(2), nullable=False),
        sa.Column("subsistema", sa.String(3), nullable=False),
        sa.Column("conjunto_id", sa.Text(), nullable=True),
        sa.Column("ceg", sa.Text(), nullable=True),
        sa.Column("potencia_mw", sa.Numeric(10, 3), nullable=True),
        sa.Column("ponto_conexao", sa.Text(), nullable=True),
        sa.Column("agente_operador", sa.Text(), nullable=True),
        _ts("atualizado_em"),
        sa.CheckConstraint("tipo IN ('usina', 'conjunto')", name=op.f("ck_usina_tipo")),
        sa.CheckConstraint("fonte IN ('eolica', 'solar')", name=op.f("ck_usina_fonte")),
        sa.CheckConstraint(
            "subsistema IN ('N', 'NE', 'S', 'SE')", name=op.f("ck_usina_subsistema")
        ),
        sa.CheckConstraint("length(uf) = 2 AND uf = upper(uf)", name=op.f("ck_usina_uf")),
        sa.ForeignKeyConstraint(
            ["conjunto_id"], ["usina.id"], name=op.f("fk_usina_conjunto_id_usina")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usina")),
        sa.UniqueConstraint("slug", name=op.f("uq_usina_slug")),
    )
    op.create_index("ix_usina_uf_fonte", "usina", ["uf", "fonte"])
    op.create_index("ix_usina_conjunto_id", "usina", ["conjunto_id"])
    op.create_index("ix_usina_nome_lower", "usina", [sa.text("lower(nome)")])
    _create_trigram_index()

    op.create_table(
        "curtailment_mensal_usina",
        sa.Column("usina_id", sa.Text(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column("geracao_verificada_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("energia_cortada_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("percentual_corte", sa.Numeric(6, 2), nullable=True),
        sa.Column("corte_rel_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("corte_cnf_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("corte_ene_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("corte_outros_mwh", sa.Numeric(14, 3), nullable=False),
        sa.Column("perda_estimada_brl", sa.Numeric(16, 2), nullable=True),
        sa.Column("base_preco", sa.Text(), nullable=True),
        sa.Column("horas_restritas", sa.Numeric(7, 1), nullable=False),
        sa.Column("percentual_corte_medio_uf", sa.Numeric(6, 2), nullable=True),
        sa.Column("posicao_nacional", sa.Integer(), nullable=True),
        sa.Column("posicao_uf", sa.Integer(), nullable=True),
        _ts("atualizado_em"),
        sa.CheckConstraint(
            "base_preco IS NULL OR base_preco IN ('PLD', 'CMO', 'PLD_FILLED', 'CMO_FILLED')",
            name=op.f("ck_curtailment_mensal_usina_base_preco"),
        ),
        _mes_check("curtailment_mensal_usina"),
        sa.ForeignKeyConstraint(
            ["usina_id"], ["usina.id"], name=op.f("fk_curtailment_mensal_usina_usina_id_usina")
        ),
        sa.PrimaryKeyConstraint("usina_id", "mes", name=op.f("pk_curtailment_mensal_usina")),
    )
    op.create_index(
        "ix_curtailment_mensal_usina_mes_posicao_nacional",
        "curtailment_mensal_usina",
        ["mes", "posicao_nacional"],
    )
    op.create_index(
        "ix_curtailment_mensal_usina_mes_posicao_uf",
        "curtailment_mensal_usina",
        ["mes", "posicao_uf"],
    )

    op.create_table(
        "curtailment_mensal_uf",
        sa.Column("uf", sa.CHAR(2), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        *_aggregate_columns(),
        _ts("atualizado_em"),
        sa.CheckConstraint(
            "fonte IN ('eolica', 'solar', 'total')", name=op.f("ck_curtailment_mensal_uf_fonte")
        ),
        sa.CheckConstraint(
            "length(uf) = 2 AND uf = upper(uf)", name=op.f("ck_curtailment_mensal_uf_uf")
        ),
        _mes_check("curtailment_mensal_uf"),
        sa.PrimaryKeyConstraint("uf", "fonte", "mes", name=op.f("pk_curtailment_mensal_uf")),
    )

    op.create_table(
        "curtailment_mensal_sin",
        sa.Column("subsistema", sa.String(3), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        *_aggregate_columns(),
        _ts("atualizado_em"),
        sa.CheckConstraint(
            "fonte IN ('eolica', 'solar', 'total')", name=op.f("ck_curtailment_mensal_sin_fonte")
        ),
        sa.CheckConstraint(
            "subsistema IN ('N', 'NE', 'S', 'SE', 'SIN')",
            name=op.f("ck_curtailment_mensal_sin_subsistema"),
        ),
        _mes_check("curtailment_mensal_sin"),
        sa.PrimaryKeyConstraint(
            "subsistema", "fonte", "mes", name=op.f("pk_curtailment_mensal_sin")
        ),
    )

    op.create_table(
        "carga_execucao",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("periodo", sa.Date(), nullable=True),
        sa.Column("arquivo", sa.Text(), nullable=True),
        sa.Column("hash", sa.Text(), nullable=True),
        sa.Column("linhas", sa.Integer(), nullable=True),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('em_andamento', 'sucesso', 'falha')", name=op.f("ck_carga_execucao_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carga_execucao")),
    )
    op.create_index("ix_carga_execucao_inicio", "carga_execucao", [sa.text("inicio DESC")])

    op.create_table(
        "relatorio_mensal",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("resumo", sa.Text(), nullable=True),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        _ts("criado_em"),
        _ts("atualizado_em"),
        _mes_check("relatorio_mensal"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relatorio_mensal")),
        sa.UniqueConstraint("slug", name=op.f("uq_relatorio_mensal_slug")),
    )

    op.create_table(
        "assinante_newsletter",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("consentimento_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("descadastrado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_confirmacao_hash", sa.Text(), nullable=True),
        sa.Column("token_confirmacao_expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_descadastro_hash", sa.Text(), nullable=False),
        sa.Column("origem", sa.Text(), nullable=True),
        _ts("criado_em"),
        sa.CheckConstraint(
            "email = lower(email)", name=op.f("ck_assinante_newsletter_email_minusculo")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assinante_newsletter")),
        sa.UniqueConstraint("email", name=op.f("uq_assinante_newsletter_email")),
    )
    op.create_index(
        "ix_assinante_newsletter_token_confirmacao_hash",
        "assinante_newsletter",
        ["token_confirmacao_hash"],
    )
    op.create_index(
        "ix_assinante_newsletter_token_descadastro_hash",
        "assinante_newsletter",
        ["token_descadastro_hash"],
    )

    op.create_table(
        "organizacao",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        _ts("criado_em"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizacao")),
    )

    op.create_table(
        "usuario",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cognito_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("organizacao_id", sa.Uuid(), nullable=True),
        sa.Column("papel", sa.Text(), nullable=False),
        _ts("criado_em"),
        sa.CheckConstraint("papel IN ('dono', 'membro', 'admin')", name=op.f("ck_usuario_papel")),
        sa.ForeignKeyConstraint(
            ["organizacao_id"],
            ["organizacao.id"],
            name=op.f("fk_usuario_organizacao_id_organizacao"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usuario")),
        sa.UniqueConstraint("cognito_sub", name=op.f("uq_usuario_cognito_sub")),
        sa.UniqueConstraint("email", name=op.f("uq_usuario_email")),
    )


def _create_trigram_index() -> None:
    """GIN trigram index for name search, only if pg_trgm can be enabled (optional)."""
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
        savepoint.rollback()  # no privilege to create the extension: keep only lower(nome)
        return
    op.create_index(
        "ix_usina_nome_trgm",
        "usina",
        [sa.text("lower(nome) gin_trgm_ops")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("usuario")
    op.drop_table("organizacao")
    op.drop_table("assinante_newsletter")
    op.drop_table("relatorio_mensal")
    op.drop_table("carga_execucao")
    op.drop_table("curtailment_mensal_sin")
    op.drop_table("curtailment_mensal_uf")
    op.drop_table("curtailment_mensal_usina")
    op.drop_table("usina")
    # pg_trgm is left installed: other objects may depend on it.
