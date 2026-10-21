"""Read queries shared by more than one router."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CurtailmentMensalUsina, Usina
from app.schemas import FonteAgregada, UsinaRanking, UsinaResumo


def latest_usina_month(session: Session, uf: str | None = None) -> date | None:
    stmt = select(CurtailmentMensalUsina.mes).order_by(CurtailmentMensalUsina.mes.desc()).limit(1)
    if uf is not None:
        stmt = stmt.join(Usina, Usina.id == CurtailmentMensalUsina.usina_id).where(Usina.uf == uf)
    return session.scalars(stmt).first()


def plant_ranking(
    session: Session, mes: date, uf: str | None, fonte: FonteAgregada, limite: int
) -> list[UsinaRanking]:
    """Top plants (tipo = 'usina') by curtailed energy in a month.

    With a specific `fonte`, `posicao` is the position computed by the pipeline
    (`posicao_uf` when filtering by UF, otherwise `posicao_nacional`). With `fonte = total`
    there is no stored cross-source position, so it is the position in this list.
    """
    stmt = (
        select(Usina, CurtailmentMensalUsina)
        .join(CurtailmentMensalUsina, CurtailmentMensalUsina.usina_id == Usina.id)
        .where(CurtailmentMensalUsina.mes == mes, Usina.tipo == "usina")
        .order_by(CurtailmentMensalUsina.energia_cortada_mwh.desc(), Usina.id)
        .limit(limite)
    )
    if uf is not None:
        stmt = stmt.where(Usina.uf == uf)
    if fonte != "total":
        stmt = stmt.where(Usina.fonte == fonte)

    itens: list[UsinaRanking] = []
    for index, (usina, linha) in enumerate(session.execute(stmt), start=1):
        stored = None
        if fonte != "total":
            stored = linha.posicao_uf if uf is not None else linha.posicao_nacional
        resumo = UsinaResumo.model_validate(usina).model_dump()
        itens.append(
            UsinaRanking.model_validate(
                {
                    **resumo,
                    "posicao": stored if stored is not None else index,
                    "energia_cortada_mwh": linha.energia_cortada_mwh,
                    "percentual_corte": linha.percentual_corte,
                    "perda_estimada_brl": linha.perda_estimada_brl,
                }
            )
        )
    return itens
