from __future__ import annotations

import asyncio
import time

import pytest

from app.cache import MemoryCache, SingleFlight, SQLiteCache, TwoTierCache, cache_key


def test_memory_cache_evicts_least_recently_used_entry() -> None:
    cache = MemoryCache(max_entries=2)
    cache.set(_entry("a"))
    cache.set(_entry("b"))
    cache.get("a")
    cache.set(_entry("c"))

    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None


def test_memory_cache_expires_entries() -> None:
    cache = MemoryCache()
    cache.set(_entry("a", ttl_seconds=0.01))

    time.sleep(0.02)

    assert cache.get("a") is None


def test_sqlite_cache_survives_new_instance(tmp_path) -> None:
    path = tmp_path / "cache.db"
    first = SQLiteCache(path)
    first.set(_entry("a"))

    second = SQLiteCache(path)

    entry = second.get("a")
    assert entry is not None
    assert entry.value == "value-a"


def test_sqlite_cache_drops_expired_entries(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.db")
    cache.set(_entry("a", ttl_seconds=0.01))

    time.sleep(0.02)

    assert cache.get("a") is None


@pytest.mark.asyncio
async def test_two_tier_cache_falls_back_to_disk(tmp_path) -> None:
    cache = TwoTierCache(database_path=tmp_path / "cache.db")
    await cache.set("key", "value", content_type="text/html")

    cache.memory.clear()

    entry = await cache.get("key")
    assert entry is not None
    assert entry.value == "value"
    assert cache.stats.disk_hits == 1


@pytest.mark.asyncio
async def test_two_tier_cache_touch_extends_expiry(tmp_path) -> None:
    cache = TwoTierCache(
        database_path=tmp_path / "cache.db",
        ttls={"text/html": 0.05},
    )
    await cache.set("key", "value", content_type="text/html")
    await cache.touch("key", ttl_seconds=60)

    entry = await cache.get("key")
    assert entry is not None
    assert entry.is_fresh()


@pytest.mark.asyncio
async def test_single_flight_shares_one_upstream_call() -> None:
    flight = SingleFlight()
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "result"

    results = await asyncio.gather(*(flight.run("same", factory) for _ in range(5)))

    assert results == ["result"] * 5
    assert calls == 1
    assert flight.coalesced_calls == 4


@pytest.mark.asyncio
async def test_single_flight_propagates_failure_to_waiters() -> None:
    flight = SingleFlight()

    async def factory() -> str:
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    results = await asyncio.gather(
        *(flight.run("same", factory) for _ in range(3)),
        return_exceptions=True,
    )

    assert all(isinstance(result, RuntimeError) for result in results)


def test_cache_key_is_stable_and_order_independent() -> None:
    first = cache_key("search", "query", 5)
    second = cache_key("search", "query", 5)

    assert first == second
    assert first != cache_key("search", "query", 6)


def _entry(key: str, *, ttl_seconds: float = 60.0):
    from app.cache import CacheEntry

    now = time.time()
    return CacheEntry(
        key=key,
        value=f"value-{key}",
        content_type="text/html",
        created_at=now,
        expires_at=now + ttl_seconds,
    )
