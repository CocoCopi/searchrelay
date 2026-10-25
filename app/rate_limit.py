from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Protocol


class SlidingWindowRateLimiter:
    """In-process sliding-window limiter (single replica only)."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(self, key: str, limit: int) -> bool:
        """Return True when a request is allowed, recording it atomically."""
        now = time.monotonic()
        cutoff = now - 1.0
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


# Atomic sliding window: prune expired entries, count, then add. The script
# keeps the check-and-record operation atomic across replicas.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window))
return 1
"""


class RedisSlidingWindowRateLimiter:
    """Redis-backed sliding-window limiter shared by every replica."""

    def __init__(self, redis: Any, *, prefix: str = "searchrelay:ratelimit") -> None:
        self.redis = redis
        self.prefix = prefix
        self._instance = uuid.uuid4().hex
        self._counter = 0

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def _member(self) -> str:
        self._counter += 1
        return f"{self._instance}:{self._counter}"

    async def check_and_record(self, key: str, limit: int) -> bool:
        now = time.time()
        allowed = await self.redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            self._key(key),
            int(limit),
            now,
            1.0,
            self._member(),
        )
        return bool(int(allowed))


class RateLimiter(Protocol):
    async def check_and_record(self, key: str, limit: int) -> bool: ...


class RateLimiterService:
    """Async facade over the in-process or Redis-backed limiter."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    @property
    def is_shared(self) -> bool:
        return isinstance(self.backend, RedisSlidingWindowRateLimiter)

    async def check_and_record(self, key: str, limit: int) -> bool:
        if isinstance(self.backend, RedisSlidingWindowRateLimiter):
            return await self.backend.check_and_record(key, limit)
        return await asyncio.to_thread(self.backend.check_and_record, key, limit)
