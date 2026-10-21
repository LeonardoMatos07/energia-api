"""ORM models. This module is the source of truth for the schema; Alembic migrations mirror it.

Types are kept portable (Postgres in production, SQLite allowed in tests).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    Uuid,
    extract,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# bigint identity in Postgres; plain INTEGER PRIMARY KEY (rowid alias) in SQLite.
BigIntId = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _now_default() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Usina(Base):
    __tablename__ = "usina"
    __table_args__ = (
        CheckConstraint("tipo IN ('usina', 'conjunto')", name="tipo"),
        CheckConstraint("fonte IN ('eolica', 'solar')", name="fonte"),
        CheckConstraint("subsistema IN ('N', 'NE', 'S', 'SE')", name="subsistema"),
        CheckConstraint("length(uf) = 2 AND uf = upper(uf)", name="uf"),
        Index("ix_usina_uf_fonte", "uf", "fonte"),
        Index("ix_usina_conjunto_id", "conjunto_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    fonte: Mapped[str] = mapped_column(Text, nullable=False)
    uf: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    subsistema: Mapped[str] = mapped_column(String(3), nullable=False)
    conjunto_id: Mapped[str | None] = mapped_column(Text, ForeignKey("usina.id"), nullable=True)
    ceg: Mapped[str | None] = mapped_column(Text)
    potencia_mw: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    ponto_conexao: Mapped[str | None] = mapped_column(Text)
    agente_operador: Mapped[str | None] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = _now_default()


# Name search uses lower(nome); the migration also adds a pg_trgm GIN index when available.
Index("ix_usina_nome_lower", func.lower(Usina.nome))


class CurtailmentMensalUsina(Base):
    __tablename__ = "curtailment_mensal_usina"
    __table_args__ = (
        CheckConstraint(
            "base_preco IS NULL OR base_preco IN ('PLD', 'CMO', 'PLD_FILLED', 'CMO_FILLED')",
            name="base_preco",
        ),
        Index("ix_curtailment_mensal_usina_mes_posicao_nacional", "mes", "posicao_nacional"),
        Index("ix_curtailment_mensal_usina_mes_posicao_uf", "mes", "posicao_uf"),
    )

    usina_id: Mapped[str] = mapped_column(Text, ForeignKey("usina.id"), primary_key=True)
    mes: Mapped[date] = mapped_column(Date, primary_key=True)
    geracao_verificada_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    energia_cortada_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    percentual_corte: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    corte_rel_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    corte_cnf_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    corte_ene_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    corte_outros_mwh: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    perda_estimada_brl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    base_preco: Mapped[str | None] = mapped_column(Text)
    horas_restritas: Mapped[Decimal] = mapped_column(Numeric(7, 1), nullable=False)
    percentual_corte_medio_uf: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    posicao_nacional: Mapped[int | None] = mapped_column(Integer)
    posicao_uf: Mapped[int | None] = mapped_column(Integer)
    atualizado_em: Mapped[datetime] = _now_default()


class _CurtailmentAggregate:
    """Columns shared by the UF and SIN aggregates."""

    fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    mes: Mapped[date] = mapped_column(Date, primary_key=True)
    geracao_verificada_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    energia_cortada_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    percentual_corte: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    corte_rel_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    corte_cnf_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    corte_ene_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    corte_outros_mwh: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    perda_estimada_brl: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    usinas_afetadas: Mapped[int] = mapped_column(Integer, nullable=False)
    atualizado_em: Mapped[datetime] = _now_default()


class CurtailmentMensalUf(_CurtailmentAggregate, Base):
    __tablename__ = "curtailment_mensal_uf"
    __table_args__ = (
        CheckConstraint("fonte IN ('eolica', 'solar', 'total')", name="fonte"),
        CheckConstraint("length(uf) = 2 AND uf = upper(uf)", name="uf"),
    )

    uf: Mapped[str] = mapped_column(CHAR(2), primary_key=True)


class CurtailmentMensalSin(_CurtailmentAggregate, Base):
    __tablename__ = "curtailment_mensal_sin"
    __table_args__ = (
        CheckConstraint("fonte IN ('eolica', 'solar', 'total')", name="fonte"),
        CheckConstraint("subsistema IN ('N', 'NE', 'S', 'SE', 'SIN')", name="subsistema"),
    )

    subsistema: Mapped[str] = mapped_column(String(3), primary_key=True)


class CargaExecucao(Base):
    __tablename__ = "carga_execucao"
    __table_args__ = (
        CheckConstraint("status IN ('em_andamento', 'sucesso', 'falha')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, Identity(), primary_key=True)
    dataset: Mapped[str] = mapped_column(Text, nullable=False)
    periodo: Mapped[date | None] = mapped_column(Date)
    arquivo: Mapped[str | None] = mapped_column(Text)
    hash: Mapped[str | None] = mapped_column(Text)
    linhas: Mapped[int | None] = mapped_column(Integer)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    detalhe: Mapped[str | None] = mapped_column(Text)


Index("ix_carga_execucao_inicio", CargaExecucao.inicio.desc())


class RelatorioMensal(Base):
    __tablename__ = "relatorio_mensal"

    id: Mapped[int] = mapped_column(BigIntId, Identity(), primary_key=True)
    mes: Mapped[date] = mapped_column(Date, nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    resumo: Mapped[str | None] = mapped_column(Text)
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = _now_default()
    atualizado_em: Mapped[datetime] = _now_default()


class AssinanteNewsletter(Base):
    __tablename__ = "assinante_newsletter"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_minusculo"),
        Index("ix_assinante_newsletter_token_confirmacao_hash", "token_confirmacao_hash"),
        Index("ix_assinante_newsletter_token_descadastro_hash", "token_descadastro_hash"),
    )

    id: Mapped[int] = mapped_column(BigIntId, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    consentimento_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    descadastrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_confirmacao_hash: Mapped[str | None] = mapped_column(Text)
    token_confirmacao_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_descadastro_hash: Mapped[str] = mapped_column(Text, nullable=False)
    origem: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = _now_default()


class Organizacao(Base):
    __tablename__ = "organizacao"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = _now_default()


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (CheckConstraint("papel IN ('dono', 'membro', 'admin')", name="papel"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cognito_sub: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    nome: Mapped[str | None] = mapped_column(Text)
    organizacao_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizacao.id"), nullable=True
    )
    papel: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = _now_default()


# "mes" columns always hold the first day of the month (contract convention).
for _name in (
    "curtailment_mensal_usina",
    "curtailment_mensal_uf",
    "curtailment_mensal_sin",
    "relatorio_mensal",
):
    _table = Base.metadata.tables[_name]
    _table.append_constraint(
        CheckConstraint(extract("day", _table.c.mes) == 1, name="mes_primeiro_dia")
    )
