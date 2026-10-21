import logging

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db import SessionDep
from app.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
def health(session: SessionDep, response: Response) -> HealthResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health: database unavailable")
        response.status_code = 503
        return HealthResponse(status="error", database="error")
    return HealthResponse(status="ok", database="ok")
