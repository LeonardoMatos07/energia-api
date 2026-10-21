import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db import SessionDep
from app.schemas import HealthResposta

logger = logging.getLogger(__name__)

router = APIRouter(tags=["interno"])


@router.get("/health", response_model=HealthResposta, responses={503: {"model": HealthResposta}})
def health(session: SessionDep, response: Response) -> HealthResposta:
    response.headers["Cache-Control"] = "no-store"
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health: banco indisponível")
        response.status_code = 503
        return HealthResposta(status="erro", banco="erro")
    return HealthResposta(status="ok", banco="ok")
