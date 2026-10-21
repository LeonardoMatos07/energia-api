from datetime import date
from typing import Any

import pytest

from app.cache import TTLCache
from app.email import EmailMessage, SesEmailSender
from app.ratelimit import RateLimiter
from app.routers.admin import slugify
from app.schemas import format_month, parse_month


def test_cache_ttl_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("app.cache.time.monotonic", lambda: clock[0])
    cache = TTLCache(ttl_seconds=10)
    calls: list[int] = []

    def factory() -> int:
        calls.append(1)
        return len(calls)

    assert cache.get_or_set("k", factory) == 1
    assert cache.get_or_set("k", factory) == 1
    clock[0] += 11
    assert cache.get_or_set("k", factory) == 2
    assert cache.clear() == 1
    assert cache.get_or_set("k", factory) == 3


def test_cache_max_entries() -> None:
    cache = TTLCache(ttl_seconds=60, max_entries=2)
    for key in "abc":
        cache.get_or_set(key, str)
    assert len(cache) == 2


def test_rate_limiter_window(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("app.ratelimit.time.monotonic", lambda: clock[0])
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("ip") and limiter.allow("ip")
    assert not limiter.allow("ip")
    assert limiter.allow("other-ip")
    clock[0] = 61
    assert limiter.allow("ip")


def test_month_parsing() -> None:
    assert parse_month("2026-09") == date(2026, 9, 1)
    assert parse_month(date(2026, 9, 15)) == date(2026, 9, 1)
    assert format_month(date(2026, 9, 1)) == "2026-09"
    with pytest.raises(ValueError, match="YYYY-MM"):
        parse_month("2026-9")


def test_slugify() -> None:
    assert slugify("Café Déjà Vu/2026: Wind & Solar") == "cafe-deja-vu-2026-wind-solar"
    assert slugify("!!!") == "report"


def test_ses_sender() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def send_email(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    fake = FakeClient()
    sender = SesEmailSender("sender@example.com", "sa-east-1", client=fake)
    sender.send(EmailMessage(to="a@b.com", subject="Subject", text="text", html="<p>x</p>"))
    call = fake.calls[0]
    assert call["FromEmailAddress"] == "sender@example.com"
    assert call["Destination"] == {"ToAddresses": ["a@b.com"]}
    assert call["Content"]["Simple"]["Subject"]["Data"] == "Subject"
