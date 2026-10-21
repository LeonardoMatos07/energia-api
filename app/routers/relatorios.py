from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.cache import PublicCacheDep
from app.db import SessionDep
from app.models import RelatorioMensal
from app.schemas import Relatorio, RelatorioResumo, RelatoriosResposta

router = APIRouter(prefix="/v1/relatorios", tags=["relatorios"])


@router.get("", response_model=RelatoriosResposta)
def list_relatorios(session: SessionDep, cache: PublicCacheDep) -> RelatoriosResposta:
    def load() -> RelatoriosResposta:
        rows = session.scalars(
            select(RelatorioMensal)
            .where(RelatorioMensal.publicado_em.is_not(None))
            .order_by(RelatorioMensal.mes.desc(), RelatorioMensal.publicado_em.desc())
        ).all()
        return RelatoriosResposta(itens=[RelatorioResumo.model_validate(r) for r in rows])

    return cache.get_or_set(load)


@router.get("/{slug}", response_model=Relatorio)
def get_relatorio(slug: str, session: SessionDep, cache: PublicCacheDep) -> Relatorio:
    def load() -> Relatorio | None:
        row = session.scalars(
            select(RelatorioMensal).where(
                RelatorioMensal.slug == slug, RelatorioMensal.publicado_em.is_not(None)
            )
        ).first()
        return Relatorio.model_validate(row) if row else None

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado.")
    return result
