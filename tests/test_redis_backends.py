from __future__ import annotations

import pytest

from app.billing import BillingService, RedisBillingStore
from app.rate_limit import RateLimiterService, RedisSlidingWindowRateLimiter

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis is a test dependency")


@pytest.fixture()
def redis_client():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        import asyncio

        asyncio.get_event_loop().run_until_complete(client.aclose())


@pytest.mark.asyncio
async def test_redis_rate_limiter_allows_up_to_the_limit(redis_client) -> None:
    limiter = RedisSlidingWindowRateLimiter(redis_client)
    service = RateLimiterService(limiter)

    assert await service.check_and_record("account:a:search", 2) is True
    assert await service.check_and_record("account:a:search", 2) is True
    assert await service.check_and_record("account:a:search", 2) is False


@pytest.mark.asyncio
async def test_redis_rate_limiter_is_shared_between_limiters(redis_client) -> None:
    first = RateLimiterService(RedisSlidingWindowRateLimiter(redis_client))
    second = RateLimiterService(RedisSlidingWindowRateLimiter(redis_client))

    assert await first.check_and_record("account:b:fetch", 1) is True
    assert await second.check_and_record("account:b:fetch", 1) is False


@pytest.mark.asyncio
async def test_redis_rate_limiter_separates_scopes(redis_client) -> None:
    limiter = RateLimiterService(RedisSlidingWindowRateLimiter(redis_client))

    assert await limiter.check_and_record("account:c:search", 1) is True
    assert await limiter.check_and_record("account:c:fetch", 1) is True


@pytest.mark.asyncio
async def test_redis_billing_charges_and_reports_usage(redis_client) -> None:
    store = RedisBillingStore(redis_client, price_micros=750)
    service = BillingService(store)
    await service.initialize({"account-a": 5000})

    assert await service.get_balance("account-a") == 5000
    assert await service.charge("account-a", "search") is True
    assert await service.get_balance("account-a") == 4250
    assert await service.usage("account-a") == {"search": 750}
    assert await service.accounts() == [("account-a", 4250)]


@pytest.mark.asyncio
async def test_redis_billing_refuses_to_oversell(redis_client) -> None:
    store = RedisBillingStore(redis_client, price_micros=750)
    service = BillingService(store)
    await service.initialize({"account-b": 1000})

    assert await service.charge("account-b", "search") is True
    assert await service.charge("account-b", "search") is False
    assert await service.get_balance("account-b") == 250


@pytest.mark.asyncio
async def test_redis_billing_rejects_unknown_accounts(redis_client) -> None:
    store = RedisBillingStore(redis_client, price_micros=750)
    service = BillingService(store)
    await service.initialize({})

    assert await service.charge("missing", "search") is False
