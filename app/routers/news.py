"""Public sector news and DOU acts, read-only for everyone, moderation for admins.

The pipeline (spillway-data) collects what the sources publish; the API only serves rows with
``status = 'published'`` and every item carries the publisher, date, link and license. The text is
returned exactly as stored: nothing is rewritten here.
"""

import base64
import logging
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import String, and_, cast, or_, select

from app.auth import AdminDep
from app.cache import PublicCacheDep, TTLCache
from app.db import SessionDep
from app.events import log_event
from app.models import NewsItem as NewsItemRow
from app.models import NewsSource as NewsSourceRow
from app.models import RegulatoryAct as RegulatoryActRow
from app.routers.admin import notify_web
from app.schemas import (
    AdminNewsItem,
    AdminNewsList,
    NewsItem,
    NewsItemDetail,
    NewsListResponse,
    NewsPatch,
    NewsSource,
    NewsSourceListResponse,
    NewsStatus,
    RegulatoryAct,
    RegulatoryActDetail,
    RegulatoryActListResponse,
    RegulatoryActPatch,
    RelatedLink,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["news"])
admin_router = APIRouter(prefix="/v1/admin", tags=["admin"])

PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
SLUG = r"^[a-z0-9-]{1,40}$"

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
TopicParam = Annotated[str | None, Query(pattern=SLUG)]
SourceParam = Annotated[str | None, Query(pattern=SLUG)]
SearchParam = Annotated[str | None, Query(min_length=2, max_length=80)]
CursorParam = Annotated[str | None, Query(max_length=120)]


def encode_cursor(moment: datetime | date, row_id: int) -> str:
    return base64.urlsafe_b64encode(f"{moment.isoformat()}|{row_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, int]:
    try:
        moment, row_id = base64.urlsafe_b64decode(cursor.encode()).decode().rsplit("|", 1)
        return moment, int(row_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid cursor.") from error


def _topic_filter(column: Any, topic: str) -> Any:
    # `topics` is a JSON list of slugs; a quoted slug cannot match part of another one.
    return cast(column, String).like(f'%"{topic}"%')


def _search_filter(columns: list[Any], text: str) -> Any:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return or_(*(column.ilike(f"%{escaped}%", escape="\\") for column in columns))


def _page_after(stmt: Any, column: Any, id_column: Any, cursor: str | None) -> Any:
    if cursor is None:
        return stmt
    moment_text, row_id = decode_cursor(cursor)
    try:
        moment: datetime | date = (
            datetime.fromisoformat(moment_text)
            if issubclass(column.type.python_type, datetime)
            else date.fromisoformat(moment_text)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid cursor.") from error
    return stmt.where(or_(column < moment, and_(column == moment, id_column < row_id)))


def _news_item(row: NewsItemRow, source: NewsSourceRow) -> NewsItem:
    return NewsItem(
        id=row.id,
        source=source.slug,
        publisher_name=source.publisher_name,
        license_code=source.license_code,
        license_url=source.license_url,
        url=row.url,
        title=row.title,
        summary=row.summary,
        published_at=_aware(row.published_at),
        source_updated_at=_aware(row.source_updated_at),
        topics=list(row.topics or []),
        has_editor_note=bool(row.editor_note),
    )


def _aware(value: datetime | None) -> Any:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _news_detail(row: NewsItemRow, source: NewsSourceRow) -> NewsItemDetail:
    return NewsItemDetail(
        **_news_item(row, source).model_dump(),
        editor_note=row.editor_note,
        editor_note_by=row.editor_note_by,
        editor_note_at=_aware(row.editor_note_at),
        related_links=[RelatedLink.model_validate(link) for link in (row.related_links or [])],
    )


def _news_query(
    *,
    topic: str | None,
    source: str | None,
    q: str | None,
    cursor: str | None,
    limit: int,
    statuses: tuple[str, ...] | None,
) -> Any:
    stmt = select(NewsItemRow, NewsSourceRow).join(
        NewsSourceRow, NewsSourceRow.id == NewsItemRow.source_id
    )
    if statuses is not None:
        stmt = stmt.where(NewsItemRow.status.in_(statuses))
    if topic:
        stmt = stmt.where(_topic_filter(NewsItemRow.topics, topic))
    if source:
        stmt = stmt.where(NewsSourceRow.slug == source)
    if q:
        stmt = stmt.where(_search_filter([NewsItemRow.title, NewsItemRow.summary], q))
    stmt = _page_after(stmt, NewsItemRow.published_at, NewsItemRow.id, cursor)
    return stmt.order_by(NewsItemRow.published_at.desc(), NewsItemRow.id.desc()).limit(limit + 1)


@router.get("/v1/news/sources", response_model=NewsSourceListResponse)
def list_sources(session: SessionDep, cache: PublicCacheDep) -> NewsSourceListResponse:
    def load() -> NewsSourceListResponse:
        rows = session.scalars(select(NewsSourceRow).order_by(NewsSourceRow.slug)).all()
        return NewsSourceListResponse(items=[NewsSource.model_validate(r) for r in rows])

    return cache.get_or_set(load)


@router.get("/v1/news", response_model=NewsListResponse)
def list_news(
    session: SessionDep,
    cache: PublicCacheDep,
    topic: TopicParam = None,
    source: SourceParam = None,
    q: SearchParam = None,
    cursor: CursorParam = None,
    limit: Limit = PAGE_SIZE,
) -> NewsListResponse:
    def load() -> NewsListResponse:
        rows = session.execute(
            _news_query(
                topic=topic,
                source=source,
                q=q,
                cursor=cursor,
                limit=limit,
                statuses=("published",),
            )
        ).all()
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1][0].published_at, page[-1][0].id) if len(rows) > limit else None
        )
        return NewsListResponse(
            items=[_news_item(item, src) for item, src in page], next_cursor=next_cursor
        )

    return cache.get_or_set(load)


@router.get("/v1/news/{item_id}", response_model=NewsItemDetail)
def get_news(item_id: int, session: SessionDep, cache: PublicCacheDep) -> NewsItemDetail:
    def load() -> NewsItemDetail | None:
        row = session.execute(
            select(NewsItemRow, NewsSourceRow)
            .join(NewsSourceRow, NewsSourceRow.id == NewsItemRow.source_id)
            .where(NewsItemRow.id == item_id, NewsItemRow.status == "published")
        ).first()
        return _news_detail(row[0], row[1]) if row else None

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="News item not found.")
    return result


def _act_query(
    *, organ: str | None, act_type: str | None, topic: str | None, cursor: str | None, limit: int
) -> Any:
    stmt = select(RegulatoryActRow).where(RegulatoryActRow.status == "published")
    if organ:
        stmt = stmt.where(RegulatoryActRow.organ == organ.upper())
    if act_type:
        stmt = stmt.where(RegulatoryActRow.act_type.ilike(act_type))
    if topic:
        stmt = stmt.where(_topic_filter(RegulatoryActRow.topics, topic))
    stmt = _page_after(stmt, RegulatoryActRow.dou_date, RegulatoryActRow.id, cursor)
    return stmt.order_by(RegulatoryActRow.dou_date.desc(), RegulatoryActRow.id.desc()).limit(
        limit + 1
    )


@router.get("/v1/regulatory-acts", response_model=RegulatoryActListResponse)
def list_acts(
    session: SessionDep,
    cache: PublicCacheDep,
    organ: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2,10}$")] = None,
    type: Annotated[str | None, Query(max_length=60)] = None,
    topic: TopicParam = None,
    cursor: CursorParam = None,
    limit: Limit = PAGE_SIZE,
) -> RegulatoryActListResponse:
    def load() -> RegulatoryActListResponse:
        rows = session.scalars(
            _act_query(organ=organ, act_type=type, topic=topic, cursor=cursor, limit=limit)
        ).all()
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1].dou_date, page[-1].id) if len(rows) > limit else None
        return RegulatoryActListResponse(
            items=[RegulatoryAct.model_validate(r) for r in page], next_cursor=next_cursor
        )

    return cache.get_or_set(load)


@router.get("/v1/regulatory-acts/{act_id}", response_model=RegulatoryActDetail)
def get_act(act_id: int, session: SessionDep, cache: PublicCacheDep) -> RegulatoryActDetail:
    def load() -> RegulatoryActDetail | None:
        row = session.scalars(
            select(RegulatoryActRow).where(
                RegulatoryActRow.id == act_id, RegulatoryActRow.status == "published"
            )
        ).first()
        return RegulatoryActDetail.model_validate(row) if row else None

    result = cache.get_or_set(load)
    if result is None:
        raise HTTPException(status_code=404, detail="Regulatory act not found.")
    return result


def _refresh_public(request: Request) -> None:
    """Moderation has to show at once: drop the API cache and tell the web."""
    cache: TTLCache = request.app.state.cache
    cache.clear()
    notify_web(request.app.state.settings, ["news"], request.app.state.web_transport)


def _audit(request: Request, action: str, entity_id: int, admin_subject: str, **props: Any) -> None:
    with request.app.state.sessionmaker() as audit:
        log_event(
            audit,
            request,
            category="admin",
            action=action,
            entity_type="news",
            entity_id=str(entity_id),
            properties={"actor": admin_subject, **props},
        )
        audit.commit()


@admin_router.get("/news", response_model=AdminNewsList)
def admin_list_news(
    _admin: AdminDep,
    session: SessionDep,
    status: NewsStatus | None = None,
    topic: TopicParam = None,
    source: SourceParam = None,
    q: SearchParam = None,
    cursor: CursorParam = None,
    limit: Limit = PAGE_SIZE,
) -> AdminNewsList:
    statuses = (status,) if status else None
    rows = session.execute(
        _news_query(topic=topic, source=source, q=q, cursor=cursor, limit=limit, statuses=statuses)
    ).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1][0].published_at, page[-1][0].id) if len(rows) > limit else None
    )
    return AdminNewsList(
        items=[
            AdminNewsItem(**_news_detail(item, src).model_dump(), status=item.status)
            for item, src in page
        ],
        next_cursor=next_cursor,
    )


@admin_router.patch("/news/{item_id}", response_model=AdminNewsItem)
def admin_patch_news(
    item_id: int, body: NewsPatch, admin: AdminDep, session: SessionDep, request: Request
) -> AdminNewsItem:
    row = session.get(NewsItemRow, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="News item not found.")
    source = session.get(NewsSourceRow, row.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="News source not found.")
    if (body.editor_note or body.clear_editor_note) and admin.kind == "machine":
        raise HTTPException(status_code=403, detail="Editor notes are written by a person.")
    if body.status is not None:
        row.status = body.status
    if body.clear_editor_note:
        row.editor_note = row.editor_note_by = row.editor_note_at = None
    elif body.editor_note is not None:
        row.editor_note = body.editor_note.strip() or None
        row.editor_note_by = admin.subject if row.editor_note else None
        row.editor_note_at = datetime.now(UTC) if row.editor_note else None
    if body.related_links is not None:
        row.related_links = [link.model_dump() for link in body.related_links]
    session.commit()
    _refresh_public(request)
    _audit(request, "admin.news.patch", item_id, admin.subject, status=row.status)
    return AdminNewsItem(**_news_detail(row, source).model_dump(), status=row.status)


@admin_router.patch("/regulatory-acts/{act_id}", response_model=RegulatoryAct)
def admin_patch_act(
    act_id: int, body: RegulatoryActPatch, admin: AdminDep, session: SessionDep, request: Request
) -> RegulatoryAct:
    row = session.get(RegulatoryActRow, act_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Regulatory act not found.")
    row.status = body.status
    session.commit()
    _refresh_public(request)
    _audit(request, "admin.regulatory_act.patch", act_id, admin.subject, status=row.status)
    return RegulatoryAct.model_validate(row)
