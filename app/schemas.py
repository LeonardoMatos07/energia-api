"""Request/response models. Keys mirror the table columns (see docs/contrato-fase1.md)."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

MES_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_MES_RE = re.compile(MES_PATTERN)

Fonte = Literal["eolica", "solar"]
FonteAgregada = Literal["eolica", "solar", "total"]
Tipo = Literal["usina", "conjunto"]
RevalidateTag = Literal["curtailment", "usinas", "relatorios"]
ALL_TAGS: list[RevalidateTag] = ["curtailment", "usinas", "relatorios"]


def parse_mes(value: Any) -> date:
    """Accepts `YYYY-MM` (API format) or a date (from the database)."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.replace(day=1)
    if isinstance(value, str) and _MES_RE.match(value):
        return date(int(value[:4]), int(value[5:7]), 1)
    raise ValueError("mês deve estar no formato YYYY-MM")


def format_mes(value: date) -> str:
    return value.strftime("%Y-%m")


Mes = Annotated[
    date,
    BeforeValidator(parse_mes),
    PlainSerializer(format_mes, return_type=str),
    WithJsonSchema({"type": "string", "pattern": MES_PATTERN, "examples": ["2026-09"]}),
]


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- curtailment -------------------------------------------------------------------------------


class _CurtailmentAgregado(_Orm):
    fonte: FonteAgregada
    mes: Mes
    geracao_verificada_mwh: float
    energia_cortada_mwh: float
    percentual_corte: float | None
    corte_rel_mwh: float
    corte_cnf_mwh: float
    corte_ene_mwh: float
    corte_outros_mwh: float
    perda_estimada_brl: float | None
    usinas_afetadas: int
    atualizado_em: datetime


class CurtailmentSin(_CurtailmentAgregado):
    subsistema: str


class CurtailmentUf(_CurtailmentAgregado):
    uf: str


class CurtailmentUsina(_Orm):
    usina_id: str
    mes: Mes
    geracao_verificada_mwh: float
    energia_cortada_mwh: float
    percentual_corte: float | None
    corte_rel_mwh: float
    corte_cnf_mwh: float
    corte_ene_mwh: float
    corte_outros_mwh: float
    perda_estimada_brl: float | None
    base_preco: str | None
    horas_restritas: float
    percentual_corte_medio_uf: float | None
    posicao_nacional: int | None
    posicao_uf: int | None
    atualizado_em: datetime


# --- usinas ------------------------------------------------------------------------------------


class UsinaResumo(_Orm):
    id: str
    tipo: Tipo
    nome: str
    slug: str
    fonte: Fonte
    uf: str
    subsistema: str
    potencia_mw: float | None


class Usina(UsinaResumo):
    conjunto_id: str | None
    ceg: str | None
    ponto_conexao: str | None
    agente_operador: str | None
    atualizado_em: datetime


class UsinaRanking(UsinaResumo):
    posicao: int
    energia_cortada_mwh: float
    percentual_corte: float | None
    perda_estimada_brl: float | None


class SinResposta(BaseModel):
    itens: list[CurtailmentSin]


class UfResposta(BaseModel):
    serie: list[CurtailmentUf]
    ranking: list[UsinaRanking]
    mes_ranking: Mes | None


class UsinasResposta(BaseModel):
    itens: list[UsinaResumo]
    total: int
    pagina: int
    tamanho: int


class UsinaDetalheResposta(BaseModel):
    usina: Usina
    conjunto: UsinaResumo | None
    usinas_do_conjunto: list[UsinaResumo]
    serie: list[CurtailmentUsina]


class RankingResposta(BaseModel):
    mes: Mes | None
    itens: list[UsinaRanking]


class MesesResposta(BaseModel):
    meses: list[Mes]


# --- relatórios --------------------------------------------------------------------------------


class RelatorioResumo(_Orm):
    mes: Mes
    titulo: str
    slug: str
    resumo: str | None
    publicado_em: datetime | None


class Relatorio(RelatorioResumo):
    conteudo: str


class RelatorioAdmin(Relatorio):
    criado_em: datetime
    atualizado_em: datetime


class RelatoriosResposta(BaseModel):
    itens: list[RelatorioResumo]


class RelatoriosAdminResposta(BaseModel):
    itens: list[RelatorioAdmin]


class RelatorioEntrada(BaseModel):
    mes: Mes
    titulo: str = Field(min_length=1, max_length=300)
    slug: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=200,
        description="Opcional; se ausente, é gerado a partir do título.",
    )
    resumo: str | None = None
    conteudo: str = Field(min_length=1)
    publicar: bool


# --- newsletter --------------------------------------------------------------------------------


class NewsletterEntrada(BaseModel):
    email: EmailStr = Field(max_length=254)
    consentimento: Literal[True]
    origem: str | None = Field(default=None, max_length=100)


class MensagemResposta(BaseModel):
    mensagem: str


class ConfirmadoResposta(BaseModel):
    confirmado: bool


class DescadastradoResposta(BaseModel):
    descadastrado: bool


# --- admin -------------------------------------------------------------------------------------


class CargaExecucao(_Orm):
    id: int
    dataset: str
    periodo: Mes | None
    arquivo: str | None
    hash: str | None
    linhas: int | None
    inicio: datetime
    fim: datetime | None
    status: Literal["em_andamento", "sucesso", "falha"]
    detalhe: str | None


class CargasResposta(BaseModel):
    itens: list[CargaExecucao]


class RevalidarEntrada(BaseModel):
    tags: list[RevalidateTag] = Field(default_factory=lambda: list(ALL_TAGS))


class RevalidarResposta(BaseModel):
    tags: list[RevalidateTag]
    cache_limpo: bool
    entradas_removidas: int
    web_revalidado: bool
    detalhe: str | None


class HealthResposta(BaseModel):
    status: str
    banco: str
