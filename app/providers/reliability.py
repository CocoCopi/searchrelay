from __future__ import annotations

import asyncio
import random
import threading
import time
from collections import deque
from dataclasses import dataclass

from app.operators import ParsedQuery
from app.providers.base import ProviderError, ProviderSearchResult, SearchProvider


class CircuitOpenError(ProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__(f"Provider {provider} circuit is open", retryable=False)


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int,
        reset_timeout_seconds: float,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout_seconds = max(0.0, reset_timeout_seconds)
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_progress = False
        self._lock = asyncio.Lock()

    async def allow_request(self) -> bool:
        async with self._lock:
            if self._opened_at is None:
                return False
            elapsed = time.monotonic() - self._opened_at
            if elapsed < self.reset_timeout_seconds:
                raise CircuitOpenError(self.name)
            if self._probe_in_progress:
                raise CircuitOpenError(self.name)
            self._probe_in_progress = True
            return True

    async def cancel_request(self) -> None:
        async with self._lock:
            self._probe_in_progress = False

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_progress = False

    async def record_failure(self) -> None:
        async with self._lock:
            if self._probe_in_progress:
                self._failures = self.failure_threshold
                self._opened_at = time.monotonic()
                self._probe_in_progress = False
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


class SlidingWindowBudget:
    def __init__(self, requests_per_minute: int) -> None:
        self.limit = max(1, requests_per_minute)
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()
            if len(self._events) >= self.limit:
                return False
            self._events.append(now)
            return True


@dataclass(frozen=True)
class ReliabilityConfig:
    timeout_seconds: float
    retry_attempts: int
    retry_base_delay_seconds: float
    circuit_failure_threshold: int
    circuit_reset_seconds: float
    requests_per_minute: int
    max_concurrency: int
    health_cache_seconds: float = 15.0


class ResilientProvider:
    def __init__(
        self,
        provider: SearchProvider,
        config: ReliabilityConfig,
    ) -> None:
        self.provider = provider
        self.name = provider.name
        self.config = config
        self._budget = SlidingWindowBudget(config.requests_per_minute)
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._breaker = CircuitBreaker(
            self.name,
            failure_threshold=config.circuit_failure_threshold,
            reset_timeout_seconds=config.circuit_reset_seconds,
        )
        self._health_checked_at: float | None = None
        self._health_result = False
        self._health_lock = asyncio.Lock()

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        attempts = max(1, self.config.retry_attempts)
        last_error: ProviderError | None = None
        owns_probe = False
        for attempt in range(attempts):
            if not owns_probe:
                owns_probe = await self._breaker.allow_request()
            if not self._budget.acquire():
                await self._breaker.cancel_request()
                raise ProviderError(
                    f"Provider {self.name} request budget exhausted",
                    retryable=False,
                )
            try:
                async with self._semaphore:
                    result = await asyncio.wait_for(
                        self.provider.search(
                            parsed,
                            count=count,
                            localization=localization,
                        ),
                        timeout=self.config.timeout_seconds,
                    )
            except asyncio.CancelledError:
                await self._breaker.cancel_request()
                raise
            except TimeoutError as exc:
                last_error = ProviderError(
                    f"Provider {self.name} timed out",
                    retryable=True,
                )
                last_error.__cause__ = exc
            except ProviderError as exc:
                last_error = exc
            except Exception as exc:
                last_error = ProviderError(
                    f"Provider {self.name} failed unexpectedly",
                    retryable=True,
                )
                last_error.__cause__ = exc
            else:
                await self._breaker.record_success()
                return result

            if last_error is None:
                raise ProviderError(f"Provider {self.name} failed")
            if attempt + 1 >= attempts or not last_error.retryable:
                await self._breaker.record_failure()
                raise last_error
            await asyncio.sleep(self._retry_delay(attempt))

        raise last_error or ProviderError(f"Provider {self.name} failed")

    async def healthcheck(self) -> bool:
        async with self._health_lock:
            now = time.monotonic()
            if (
                self._health_checked_at is not None
                and now - self._health_checked_at < self.config.health_cache_seconds
            ):
                return self._health_result
            try:
                result = await asyncio.wait_for(
                    self.provider.healthcheck(),
                    timeout=self.config.timeout_seconds,
                )
            except Exception:
                result = False
            self._health_result = result
            self._health_checked_at = time.monotonic()
            return result

    def _retry_delay(self, attempt: int) -> float:
        if self.config.retry_base_delay_seconds <= 0:
            return 0.0
        ceiling = self.config.retry_base_delay_seconds * (2**attempt)
        return random.uniform(0.0, ceiling)
