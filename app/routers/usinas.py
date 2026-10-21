from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import CurtailmentMensalSin, CurtailmentMensalUsina, Usina
from app.queries import latest_usina_month, plant_ranking
from app.schemas import (
    MES_PATTERN,
    CurtailmentUsina,
    Fonte,
    FonteAgregada,
    MesesResposta,
    RankingResposta,
    Tipo,
    UsinaDetalheResposta,
    UsinaResumo,
    UsinasResposta,
    parse_mes,
)
from app.schemas import Usina as UsinaSchema

router = APIRouter(prefix="/v1", tags=["usinas"])


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/usinas", response_model=UsinasResposta)
def list_usinas(
    session: SessionDep,
    cache: PublicCacheDep,
    busca: Annotated[str | None, Query(max_length=100)] = None,
    uf: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    fonte: Fonte | None = None,
    tipo: Tipo | None = None,
    pagina: Annotated[int, Query(ge=1)] = 1,
    tamanho: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UsinasResposta:
    def load() -> UsinasResposta:
        filters = []
        if busca and busca.strip():
            pattern = f"%{_escape_like(busca.strip().lower())}%"
            filters.append(
                or_(
                    func.lower(Usina.nome).like(pattern, escape="\\"),
                    func.lower(Usina.id).like(pattern, escape="\\"),
                )
            )
        if uf is not None:
            filters.append(Usina.uf == uf.upper())
        if fonte is not None:
            filters.append(Usina.fonte == fonte)
        if tipo is not None:
            filters.append(Usina.tipo == tipo)

        total = session.scalar(select(func.count()).select_from(Usina).where(*filters)) or 0
        rows = session.scalars(
            select(Usina)
            .where(*filters)
            .order_by(Usina.nome, Usina.id)
            .offset((pagina - 1) * tamanho)
            .limit(tamanho)
        ).all()
        return UsinasResposta(
            itens=[UsinaResumo.model_validate(r) for r in rows],
            total=total,
            pagina=pagina,
            tamanho=tamanho,
        )

    return cache.get_or_set(load)


@router.get("/usinas/{slug}", response_model=UsinaDetalheResposta)
def get_usina(slug: str, session: SessionDep, cache: PublicCacheDep) -> UsinaDetalheResposta:
    def load() -> UsinaDetalheResposta | None:
        usina = session.scalars(select(Usina).where(Usina.slug == slug)).first()
        if usina is None:
            return None
        conjunto = session.get(Usina, usina.conjunto_id) if usina.conjunto_id else None
        # Members of the cluster: the plant itself if it is a cluster, else its parent cluster.
        conjunto_id = usina.id if usina.tipo == "conjunto" else usina.conjunto_id
        membros = (
            session.scalars(
                select(Usina).where(Usina.conjunto_id == conjunto_id).order_by(Usina.nome, Usina.id)
            ).all()
            if conjunto_id
            else []
        )
        serie = session.scalars(
            select(CurtailmentMensalUsina)
            .where(CurtailmentMensalUsina.usina_id == usina.id)
            .order_by(CurtailmentMensalUsina.mes)
        ).all()
        return UsinaDetalheResposta(
            usina=UsinaSchema.model_validate(usina),
            conjunto=UsinaResumo.model_validate(conjunto) if conjunto else None,
            usinas_do_conjunto=[UsinaResumo.model_validate(m) for m in membros],
            serie=[CurtailmentUsina.model_validate(s) for s in serie],
        )

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Usina não encontrada.")
    return result


@router.get("/ranking", response_model=RankingResposta)
def ranking(
    session: SessionDep,
    cache: PublicCacheDep,
    mes: Annotated[str | None, Query(pattern=MES_PATTERN, examples=["2026-09"])] = None,
    uf: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")] = None,
    fonte: FonteAgregada = "total",
    limite: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RankingResposta:
    def load() -> RankingResposta:
        uf_upper = uf.upper() if uf else None
        mes_ranking = parse_mes(mes) if mes is not None else latest_usina_month(session)
        if mes_ranking is None:
            return RankingResposta(mes=None, itens=[])
        return RankingResposta(
            mes=mes_ranking,
            itens=plant_ranking(session, mes_ranking, uf_upper, fonte, limite),
        )

    return cache.get_or_set(load)


@router.get("/meses", response_model=MesesResposta, tags=["curtailment"])
def meses(session: SessionDep, cache: PublicCacheDep) -> MesesResposta:
    def load() -> MesesResposta:
        stmt = select(CurtailmentMensalSin.mes).union(select(CurtailmentMensalUsina.mes))
        months = sorted(set(session.scalars(stmt).all()), reverse=True)
        return MesesResposta(meses=months)

    return cache.get_or_set(load)
