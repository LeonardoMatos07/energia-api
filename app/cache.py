import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request, Response

PUBLIC_CACHE_CONTROL = "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 1024) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_set[T](self, key: str, factory: Callable[[], T]) -> T:
        if self.ttl <= 0:
            return factory()
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and hit[0] > now:
                self._data.move_to_end(key)
                value: T = hit[1]
                return value
        value = factory()
        with self._lock:
            self._data[key] = (now + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
        return value

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            return count

    def __len__(self) -> int:
        return len(self._data)


def get_cache(request: Request) -> TTLCache:
    cache: TTLCache = request.app.state.cache
    return cache


def public_cache(request: Request, response: Response) -> "PublicCache":
    response.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL
    return PublicCache(get_cache(request), request)


class PublicCache:
    def __init__(self, cache: TTLCache, request: Request) -> None:
        self._cache = cache
        query = sorted(request.query_params.multi_items())
        self.key = f"{request.url.path}?{query}"

    def get_or_set[T](self, factory: Callable[[], T]) -> T:
        return self._cache.get_or_set(self.key, factory)


PublicCacheDep = Annotated[PublicCache, Depends(public_cache)]
