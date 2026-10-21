from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy import select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import CurtailmentMensalSin, CurtailmentMensalUf
from app.queries import latest_usina_month, plant_ranking
from app.schemas import (
    MES_PATTERN,
    CurtailmentSin,
    CurtailmentUf,
    FonteAgregada,
    SinResposta,
    UfResposta,
    parse_mes,
)

router = APIRouter(prefix="/v1/curtailment", tags=["curtailment"])

# fmt: off
UFS = frozenset({
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
})
# fmt: on
RANKING_UF_LIMIT = 20

MesQuery = Annotated[str | None, Query(pattern=MES_PATTERN, examples=["2026-09"])]


@router.get("/sin", response_model=SinResposta)
def curtailment_sin(
    session: SessionDep,
    cache: PublicCacheDep,
    fonte: FonteAgregada = "total",
    inicio: MesQuery = None,
    fim: MesQuery = None,
) -> SinResposta:
    def load() -> SinResposta:
        stmt = (
            select(CurtailmentMensalSin)
            .where(CurtailmentMensalSin.fonte == fonte)
            .order_by(CurtailmentMensalSin.mes, CurtailmentMensalSin.subsistema)
        )
        if inicio is not None:
            stmt = stmt.where(CurtailmentMensalSin.mes >= parse_mes(inicio))
        if fim is not None:
            stmt = stmt.where(CurtailmentMensalSin.mes <= parse_mes(fim))
        rows = session.scalars(stmt).all()
        return SinResposta(itens=[CurtailmentSin.model_validate(r) for r in rows])

    return cache.get_or_set(load)


@router.get("/uf/{uf}", response_model=UfResposta)
def curtailment_uf(
    session: SessionDep,
    cache: PublicCacheDep,
    uf: Annotated[str, Path(pattern=r"^[A-Za-z]{2}$")],
    fonte: FonteAgregada = "total",
    mes: MesQuery = None,
) -> UfResposta:
    uf = uf.upper()
    if uf not in UFS:
        raise HTTPException(status_code=404, detail="UF não encontrada.")

    def load() -> UfResposta:
        rows = session.scalars(
            select(CurtailmentMensalUf)
            .where(CurtailmentMensalUf.uf == uf, CurtailmentMensalUf.fonte == fonte)
            .order_by(CurtailmentMensalUf.mes)
        ).all()
        mes_ranking = parse_mes(mes) if mes is not None else latest_usina_month(session, uf)
        ranking = (
            plant_ranking(session, mes_ranking, uf, fonte, RANKING_UF_LIMIT)
            if mes_ranking is not None
            else []
        )
        return UfResposta(
            serie=[CurtailmentUf.model_validate(r) for r in rows],
            ranking=ranking,
            mes_ranking=mes_ranking,
        )

    return cache.get_or_set(load)
