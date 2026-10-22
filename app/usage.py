import csv
import io
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, case, delete, distinct, func, select
from sqlalchemy.orm import Session

from app.models import Member, UsageEvent
from app.schemas import (
    ActiveLead,
    CountItem,
    DailyUsage,
    EntityUsage,
    FunnelStep,
    RoleUsage,
    SectionUsage,
    UsageSummary,
    UsageTotals,
)

E = UsageEvent
TOP_ENTITIES = 10
TOP_ITEMS = 15
TOP_LEADS = 15
SAMPLE_LIMIT = 5000
ENTITY_TYPES = ("state", "plant", "report")
CSV_COLUMNS = [
    "id",
    "occurred_at",
    "category",
    "action",
    "section",
    "entity_type",
    "entity_id",
    "path",
    "member_id",
    "member_email",
    "member_role",
    "member_segment",
    "visitor_id",
    "session_key",
    "device",
    "is_bot",
    "referrer",
    "request_id",
    "properties",
]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _day(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def usage_summary(
    session: Session, days: int, include_bots: bool = False, now: datetime | None = None
) -> UsageSummary:
    end = now or datetime.now(UTC)
    start = end - timedelta(days=days)
    where = [E.occurred_at >= start]
    if not include_bots:
        where.append(E.is_bot.is_(False))

    def scalar(expression: Any, *extra: Any) -> int:
        return int(session.scalar(select(expression).where(*where, *extra)) or 0)

    totals = UsageTotals(
        events=scalar(func.count()),
        page_views=scalar(func.count(), E.category == "page"),
        members_active=scalar(func.count(distinct(E.member_id))),
        visitors=scalar(func.count(distinct(E.visitor_id))),
        sessions=scalar(func.count(distinct(E.session_key))),
        new_members=int(
            session.scalar(
                select(func.count()).select_from(Member).where(Member.created_at >= start)
            )
            or 0
        ),
    )

    day = func.date(E.occurred_at)
    daily = [
        DailyUsage(
            day=_day(row[0]),
            page_views=int(row[1] or 0),
            interactions=int(row[2] or 0),
            members=int(row[3] or 0),
        )
        for row in session.execute(
            select(
                day,
                func.sum(case((E.category == "page", 1), else_=0)),
                func.sum(case((E.category == "interaction", 1), else_=0)),
                func.count(distinct(E.member_id)),
            )
            .where(*where)
            .group_by(day)
            .order_by(day)
        )
    ]

    sections = [
        SectionUsage(section=row[0], views=int(row[1]), members=int(row[2]))
        for row in session.execute(
            select(E.section, func.count(), func.count(distinct(E.member_id)))
            .where(*where, E.category == "page", E.section.is_not(None))
            .group_by(E.section)
            .order_by(func.count().desc())
        )
    ]

    entities: list[EntityUsage] = []
    for entity_type in ENTITY_TYPES:
        entities.extend(
            EntityUsage(
                entity_type=entity_type, entity_id=row[0], views=int(row[1]), members=int(row[2])
            )
            for row in session.execute(
                select(E.entity_id, func.count(), func.count(distinct(E.member_id)))
                .where(
                    *where,
                    E.category == "page",
                    E.entity_type == entity_type,
                    E.entity_id.is_not(None),
                )
                .group_by(E.entity_id)
                .order_by(func.count().desc(), E.entity_id)
                .limit(TOP_ENTITIES)
            )
        )

    interactions = [
        CountItem(name=row[0], count=int(row[1]))
        for row in session.execute(
            select(E.action, func.count())
            .where(*where, E.category == "interaction")
            .group_by(E.action)
            .order_by(func.count().desc(), E.action)
            .limit(20)
        )
    ]

    searches: Counter[str] = Counter()
    for (properties,) in session.execute(
        select(E.properties)
        .where(*where, E.action == "search.plants")
        .order_by(E.occurred_at.desc())
        .limit(SAMPLE_LIMIT)
    ):
        term = str((properties or {}).get("q", "")).strip().lower()
        if term:
            searches[term] += 1

    compared: Counter[str] = Counter()
    for (properties,) in session.execute(
        select(E.properties)
        .where(*where, E.action.in_(("compare.configure", "page.view")), E.section == "comparar")
        .order_by(E.occurred_at.desc())
        .limit(SAMPLE_LIMIT)
    ):
        for token in (properties or {}).get("entities", []) or []:
            compared[str(token)] += 1

    def distinct_count(column: Any, *extra: Any) -> int:
        return scalar(func.count(distinct(column)), *extra)

    returning = int(
        session.scalar(
            select(func.count()).select_from(
                select(E.member_id)
                .where(*where, E.member_id.is_not(None))
                .group_by(E.member_id)
                .having(func.count(distinct(day)) >= 2)
                .subquery()
            )
        )
        or 0
    )
    funnel = [
        FunnelStep(
            step="Visitaram a página inicial",
            count=distinct_count(E.visitor_id, E.category == "page", E.section == "inicio"),
        ),
        FunnelStep(
            step="Abriram o cadastro",
            count=distinct_count(E.visitor_id, E.category == "page", E.section == "cadastro"),
        ),
        FunnelStep(
            step="Enviaram o cadastro", count=scalar(func.count(), E.action == "auth.signup")
        ),
        FunnelStep(
            step="Confirmaram o e-mail", count=scalar(func.count(), E.action == "auth.verify")
        ),
        FunnelStep(
            step="Entraram na conta", count=distinct_count(E.member_id, E.action == "auth.login")
        ),
        FunnelStep(step="Voltaram em 2+ dias", count=returning),
    ]

    roles = [
        RoleUsage(role=row[0], segment=row[1], members=int(row[2]), events=int(row[3]))
        for row in session.execute(
            select(E.member_role, E.member_segment, func.count(distinct(E.member_id)), func.count())
            .where(*where, E.member_id.is_not(None))
            .group_by(E.member_role, E.member_segment)
            .order_by(func.count().desc())
        )
    ]

    active_leads = [
        ActiveLead(
            member_id=row[0].id,
            name=row[0].name,
            email=row[0].email,
            organization=row[0].organization,
            role=row[0].role,
            lead_tier=row[0].lead_tier,
            lead_score=row[0].lead_score,
            events=int(row[1]),
            last_seen=row[2],
        )
        for row in session.execute(
            select(Member, func.count(E.id), func.max(E.occurred_at))
            .join(E, E.member_id == Member.id)
            .where(*where, Member.lead_tier.in_(("hot", "warm")))
            .group_by(Member.id)
            .order_by(Member.lead_score.desc(), func.count(E.id).desc())
            .limit(TOP_LEADS)
        )
    ]

    return UsageSummary(
        start=start,
        end=end,
        days=days,
        include_bots=include_bots,
        totals=totals,
        daily=daily,
        sections=sections,
        entities=entities,
        searches=[CountItem(name=k, count=v) for k, v in searches.most_common(TOP_ITEMS)],
        interactions=interactions,
        compared=[CountItem(name=k, count=v) for k, v in compared.most_common(TOP_ITEMS)],
        funnel=funnel,
        roles=roles,
        active_leads=active_leads,
    )


def event_filters(
    since: datetime | None,
    action: str | None,
    section: str | None,
    member_id: uuid.UUID | None,
    include_bots: bool,
) -> list[Any]:
    conditions: list[Any] = []
    if since is not None:
        conditions.append(E.occurred_at >= since)
    if action:
        conditions.append(E.action == action)
    if section:
        conditions.append(E.section == section)
    if member_id is not None:
        conditions.append(E.member_id == member_id)
    if not include_bots:
        conditions.append(E.is_bot.is_(False))
    return conditions


def events_csv(session: Session, since: datetime | None, limit: int) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    rows = session.execute(
        select(E, Member.email)
        .outerjoin(Member, Member.id == E.member_id)
        .where(*event_filters(since, None, None, None, True))
        .order_by(E.occurred_at, E.id)
        .limit(limit)
    )
    for event, email in rows:
        writer.writerow(
            [
                event.id,
                _aware(event.occurred_at).isoformat(),
                event.category,
                event.action,
                event.section,
                event.entity_type,
                event.entity_id,
                event.path,
                event.member_id,
                email,
                event.member_role,
                event.member_segment,
                event.visitor_id,
                event.session_key,
                event.device,
                event.is_bot,
                event.referrer,
                event.request_id,
                json.dumps(event.properties, ensure_ascii=False),
            ]
        )
    return buffer.getvalue()


def purge_events(session: Session, days: int, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    result = cast(CursorResult[Any], session.execute(delete(E).where(E.occurred_at < cutoff)))
    session.commit()
    return int(result.rowcount or 0)
