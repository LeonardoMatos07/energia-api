import hashlib
import hmac
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Member, UsageEvent

WEB_AUTH_HEADER = "x-web-api-secret"
CLIENT_IP_HEADER = "x-client-ip"
VISITOR_HEADER = "x-visitor-id"
SESSION_KEY_HEADER = "x-session-key"
USER_AGENT_HEADER = "x-user-agent"
REFERRER_HEADER = "x-referrer"

DEFAULT_HASH_KEY = "spillway-local"
MAX_TEXT = 512
MAX_USER_AGENT = 300
MAX_PROPERTIES_BYTES = 4000

SECTION_BY_SEGMENT = {
    "": "inicio",
    "cadastro": "cadastro",
    "verificar-email": "cadastro",
    "entrar": "entrar",
    "esqueci-a-senha": "entrar",
    "redefinir-senha": "entrar",
    "painel": "painel",
    "estados": "estados",
    "usinas": "usinas",
    "comparar": "comparar",
    "relatorios": "relatorios",
    "analises": "analises",
    "conta": "conta",
    "metodologia": "metodologia",
    "privacidade": "privacidade",
    "newsletter": "newsletter",
}
ENTITY_BY_SEGMENT = {"estados": "state", "usinas": "plant", "relatorios": "report"}
SAFE_QUERY_KEYS = frozenset(
    {
        "de",
        "ate",
        "fonte",
        "escopo",
        "aba",
        "itens",
        "metricas",
        "modo",
        "grafico",
        "ano",
        "unidade",
    }
)
BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|headless|lighthouse|preview|monitor|uptime|facebookexternalhit",
    re.IGNORECASE,
)
TABLET_PATTERN = re.compile(r"ipad|tablet", re.IGNORECASE)
MOBILE_PATTERN = re.compile(r"mobi|android|iphone", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    section: str
    entity_type: str | None = None
    entity_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventContext:
    member: Member | None = None
    visitor_id: str | None = None
    session_key: str | None = None
    ip_hash: str | None = None
    user_agent: str | None = None
    referrer: str | None = None
    request_id: str | None = None


def clip(value: str | None, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:limit] if value else None


def classify_path(path: str, query: Mapping[str, str] | str | None = None) -> Classification:
    pairs = dict(parse_qsl(query)) if isinstance(query, str) else dict(query or {})
    segments = [part for part in path.split("?")[0].split("/") if part]
    first = segments[0] if segments else ""
    section = SECTION_BY_SEGMENT.get(first, "outros")
    properties: dict[str, Any] = {
        key: clip(value, 200) for key, value in pairs.items() if key in SAFE_QUERY_KEYS
    }
    entity_type = ENTITY_BY_SEGMENT.get(first)
    entity_id = clip(segments[1], 160) if entity_type and len(segments) > 1 else None
    if entity_type == "state" and entity_id:
        entity_id = entity_id.upper()
    if section == "comparar" and pairs.get("itens"):
        entity_type = "comparison"
        entity_id = clip(pairs["itens"], 200)
        properties["entities"] = [item for item in pairs["itens"].split(",") if item][:12]
    return Classification(section, entity_type, entity_id, properties)


def detect_device(user_agent: str | None) -> tuple[str | None, bool]:
    if not user_agent:
        return None, False
    if BOT_PATTERN.search(user_agent):
        return "bot", True
    if TABLET_PATTERN.search(user_agent):
        return "tablet", False
    if MOBILE_PATTERN.search(user_agent):
        return "mobile", False
    return "desktop", False


def hash_ip(ip: str | None, secret: str) -> str | None:
    if not ip:
        return None
    key = (secret or DEFAULT_HASH_KEY).encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:32]


def trusted_web(request: Request, settings: Settings) -> bool:
    if not settings.WEB_API_SECRET:
        return False
    provided = request.headers.get(WEB_AUTH_HEADER, "")
    return hmac.compare_digest(provided.encode(), settings.WEB_API_SECRET.encode())


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def context_from_request(
    request: Request, settings: Settings, member: Member | None = None
) -> EventContext:
    trusted = trusted_web(request, settings)
    headers = request.headers
    if trusted:
        ip = _valid_ip(headers.get(CLIENT_IP_HEADER))
        user_agent = headers.get(USER_AGENT_HEADER)
        referrer = headers.get(REFERRER_HEADER)
        visitor = headers.get(VISITOR_HEADER)
        session_key = headers.get(SESSION_KEY_HEADER)
    else:
        ip = request.client.host if request.client else None
        user_agent = headers.get("user-agent")
        referrer = headers.get("referer")
        visitor = None
        session_key = None
    return EventContext(
        member=member,
        visitor_id=clip(visitor, 64),
        session_key=clip(session_key, 64),
        ip_hash=hash_ip(ip, settings.EVENT_HASH_SECRET),
        user_agent=clip(user_agent, MAX_USER_AGENT),
        referrer=clip(referrer),
    )


def safe_properties(properties: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(properties or {})
    try:
        if len(json.dumps(data, default=str)) <= MAX_PROPERTIES_BYTES:
            safe: dict[str, Any] = json.loads(json.dumps(data, default=str))
            return safe
    except (TypeError, ValueError):
        pass
    return {"truncated": True}


def build_event(
    context: EventContext,
    *,
    category: str,
    action: str,
    section: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    path: str | None = None,
    properties: Mapping[str, Any] | None = None,
) -> UsageEvent:
    device, is_bot = detect_device(context.user_agent)
    member = context.member
    return UsageEvent(
        category=category,
        action=action,
        section=section,
        entity_type=entity_type,
        entity_id=entity_id,
        path=clip(path),
        properties=safe_properties(properties),
        member_id=member.id if member is not None else None,
        member_role=member.role if member is not None else None,
        member_segment=member.segment if member is not None else None,
        visitor_id=context.visitor_id,
        session_key=context.session_key,
        ip_hash=context.ip_hash,
        user_agent=context.user_agent,
        device=device,
        is_bot=is_bot,
        referrer=context.referrer,
        request_id=context.request_id,
    )


def log_event(
    session: Session,
    request: Request,
    *,
    category: str,
    action: str,
    member: Member | None = None,
    section: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    properties: Mapping[str, Any] | None = None,
    anonymous: bool = False,
) -> None:
    settings: Settings = request.app.state.settings
    context = EventContext() if anonymous else context_from_request(request, settings, member)
    session.add(
        build_event(
            context,
            category=category,
            action=action,
            section=section,
            entity_type=entity_type,
            entity_id=entity_id,
            path=request.url.path,
            properties=properties,
        )
    )
