"""Request rate limiting (Phase 13).

A small **fixed-window** limiter, applied per route rather than globally: the
sensitive surfaces are the ones worth protecting — credential endpoints (where
an attacker would otherwise be free to guess passwords) and AI generation
(where every call costs money or quota).

The counter lives in Redis so the limit is shared across worker processes. If
Redis is unreachable the limiter degrades to an in-process counter and keeps
enforcing a per-instance limit, rather than either failing open (no protection)
or failing closed (the API stops working because a cache is down). Aggressive
limits are still bounded; only the cross-process precision is lost.

Usage::

    @router.post("/login", dependencies=[Depends(rate_limit("login", limit=10))])
    def login(...): ...
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Redis key prefix, so rate-limit counters are easy to spot and flush.
_KEY_PREFIX = "ratelimit"


class CounterStore(Protocol):
    """Minimal backend for a fixed-window counter."""

    def hit(self, key: str, window_seconds: int) -> int:
        """Increment the counter and return its value inside the current window."""
        ...


class MemoryStore:
    """An in-process fixed-window counter.

    Used as the fallback when Redis is unavailable and as the store in tests. A
    window is identified by its key; the whole counter expires ``window_seconds``
    after the first hit.
    """

    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, window_seconds: int) -> int:
        now = time.monotonic()
        with self._lock:
            count, expires_at = self._counts.get(key, (0, now + window_seconds))
            if now >= expires_at:
                count, expires_at = 0, now + window_seconds
            count += 1
            self._counts[key] = (count, expires_at)
            # Opportunistic prune so a long-lived process cannot grow unbounded.
            if len(self._counts) > 10_000:
                self._counts = {
                    k: v for k, v in self._counts.items() if v[1] > now
                }
            return count


class RedisStore:
    """A fixed-window counter backed by Redis.

    ``INCR`` + a one-time ``EXPIRE`` is atomic enough for rate limiting: two
    counters can only differ by the requests racing within the same window.
    Any Redis error is contained — the store falls back to an in-process counter
    and logs once, so a cache outage never takes the API down.
    """

    def __init__(self, client) -> None:
        self._client = client
        self._fallback = MemoryStore()
        self._logged_fallback = False

    def hit(self, key: str, window_seconds: int) -> int:
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)
            # ``nx=True`` sets the TTL only when the key has none, so the window
            # is not extended on every request within it.
            pipe.expire(key, window_seconds, nx=True)
            count, _ = pipe.execute()
            return int(count)
        except Exception as exc:  # noqa: BLE001 - any Redis error degrades gracefully
            if not self._logged_fallback:
                logger.warning("rate limiter falling back to in-process store: %s", exc)
                self._logged_fallback = True
            return self._fallback.hit(key, window_seconds)


class RateLimiter:
    """Fixed-window limiter over a :class:`CounterStore`."""

    def __init__(self, store: CounterStore) -> None:
        self._store = store

    def hit(self, key: str, window_seconds: int) -> int:
        return self._store.hit(key, window_seconds)

    def check(self, key: str, limit: int, window_seconds: int) -> int:
        """Register a hit and return the ``Retry-After`` value, or ``0`` if allowed."""
        count = self.hit(key, window_seconds)
        return 0 if count <= limit else window_seconds


_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def _build_store() -> CounterStore:
    """A Redis store when Redis answers, otherwise an in-process store."""
    try:
        import redis  # imported lazily so a unit test needs no Redis dependency

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        return RedisStore(client)
    except Exception as exc:  # noqa: BLE001 - missing Redis must not break the API
        logger.info("rate limiter using in-process store (Redis unavailable: %s)", exc)
        return MemoryStore()


def get_limiter() -> RateLimiter:
    """The process-wide limiter (Redis-backed when available)."""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = RateLimiter(_build_store())
    return _limiter


def client_identity(request: Request) -> str:
    """Best-effort caller identity: the first hop of ``X-Forwarded-For`` or the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(
    bucket: str,
    *,
    limit: int,
    window_seconds: int | None = None,
) -> Callable[[Request], None]:
    """Build a FastAPI dependency that enforces one limit for one bucket.

    ``bucket`` names the surface (``login``, ``ai-generate``, ...) so different
    routes do not share a counter. The limit is keyed on the caller's address.
    """

    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        window = window_seconds or settings.rate_limit_window_seconds
        key = f"{_KEY_PREFIX}:{bucket}:{client_identity(request)}"
        retry_after = get_limiter().check(key, limit, window)
        if retry_after:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


__all__ = [
    "CounterStore",
    "MemoryStore",
    "RateLimiter",
    "RedisStore",
    "client_identity",
    "get_limiter",
    "rate_limit",
]
