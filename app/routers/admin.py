import logging
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.auth import AdminDep
from app.cache import TTLCache
from app.config import Settings
from app.db import SessionDep
from app.models import CargaExecucao, RelatorioMensal
from app.schemas import (
    CargaExecucao as CargaExecucaoSchema,
)
from app.schemas import (
    CargasResposta,
    RelatorioAdmin,
    RelatorioEntrada,
    RelatoriosAdminResposta,
    RevalidarEntrada,
    RevalidarResposta,
    RevalidateTag,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "relatorio"


@router.get("/cargas", response_model=CargasResposta)
def list_cargas(
    _admin: AdminDep,
    session: SessionDep,
    limite: Annotated[int, Query(ge=1, le=500)] = 50,
) -> CargasResposta:
    rows = session.scalars(
        select(CargaExecucao)
        .order_by(CargaExecucao.inicio.desc(), CargaExecucao.id.desc())
        .limit(limite)
    ).all()
    return CargasResposta(itens=[CargaExecucaoSchema.model_validate(r) for r in rows])


@router.get("/relatorios", response_model=RelatoriosAdminResposta)
def list_all_relatorios(_admin: AdminDep, session: SessionDep) -> RelatoriosAdminResposta:
    rows = session.scalars(
        select(RelatorioMensal).order_by(RelatorioMensal.mes.desc(), RelatorioMensal.id.desc())
    ).all()
    return RelatoriosAdminResposta(itens=[RelatorioAdmin.model_validate(r) for r in rows])


@router.post("/relatorios", response_model=RelatorioAdmin)
def upsert_relatorio(
    body: RelatorioEntrada, admin: AdminDep, session: SessionDep, request: Request
) -> RelatorioAdmin:
    slug = body.slug or slugify(body.titulo)
    now = datetime.now(UTC)
    row = session.scalars(select(RelatorioMensal).where(RelatorioMensal.slug == slug)).first()
    if row is None:
        row = RelatorioMensal(slug=slug, criado_em=now)
        session.add(row)
    row.mes = body.mes
    row.titulo = body.titulo
    row.resumo = body.resumo
    row.conteudo = body.conteudo
    row.atualizado_em = now
    if body.publicar:
        row.publicado_em = row.publicado_em or now  # keep the original publication date
    else:
        row.publicado_em = None
    session.commit()
    session.refresh(row)
    cache: TTLCache = request.app.state.cache
    cache.clear()
    logger.info("relatório %s salvo por %s (%s)", slug, admin.subject, admin.kind)
    return RelatorioAdmin.model_validate(row)


def notify_web(
    settings: Settings, tags: list[RevalidateTag], transport: httpx.BaseTransport | None = None
) -> tuple[bool, str | None]:
    """POSTs the tags to energia-web. Returns (reached, error detail)."""
    if not settings.REVALIDATE_SECRET:
        return False, "REVALIDATE_SECRET não configurado; energia-web não foi chamado."
    url = f"{settings.web_url}/api/revalidar"
    try:
        with httpx.Client(timeout=settings.REVALIDATE_TIMEOUT_SEGUNDOS, transport=transport) as c:
            response = c.post(
                url,
                json={"tags": tags},
                headers={"x-revalidate-secret": settings.REVALIDATE_SECRET},
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("falha ao revalidar o energia-web (%s): %s", url, exc)
        return False, f"falha ao chamar o energia-web: {type(exc).__name__}"
    return True, None


@router.post("/revalidar", response_model=RevalidarResposta)
def revalidar(
    admin: AdminDep, request: Request, body: RevalidarEntrada | None = None
) -> RevalidarResposta:
    tags = (body or RevalidarEntrada()).tags
    cache: TTLCache = request.app.state.cache
    removed = cache.clear()
    settings: Settings = request.app.state.settings
    reached, detail = notify_web(settings, tags, request.app.state.web_transport)
    logger.info("revalidação %s por %s: web=%s", tags, admin.subject, reached)
    return RevalidarResposta(
        tags=tags,
        cache_limpo=True,
        entradas_removidas=removed,
        web_revalidado=reached,
        detalhe=detail,
    )
