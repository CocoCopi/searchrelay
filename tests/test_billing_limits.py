from __future__ import annotations

import httpx
import pytest

from tests.helpers import JSON_HEADERS, api_harness, search_result, searxng_response


@pytest.mark.asyncio
async def test_successful_search_charges_exactly_one_request_price(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return searxng_response([search_result()])

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "test", "count": 10},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 200
    assert before - after == 750


@pytest.mark.asyncio
async def test_empty_search_results_are_free(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return searxng_response([])

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "test"},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 200
    assert before == after


@pytest.mark.asyncio
async def test_failed_search_is_free(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return searxng_response(status_code=500)

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "test"},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 504
    assert before == after


@pytest.mark.asyncio
async def test_account_without_balance_returns_402(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return searxng_response([search_result()])

    settings = {
        "initial_balances_json": {"account-test": 0},
    }
    async with api_harness(handler, tmp_path, **settings) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "test"},
        )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "insufficient_balance"


@pytest.mark.asyncio
async def test_search_qps_limit_returns_429(tmp_path):
    upstream_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return searxng_response([])

    async with api_harness(handler, tmp_path, search_qps=1) as (client, _):
        first = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "first"},
        )
        second = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "second"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "qps_exceeded"
    assert upstream_calls == 1


@pytest.mark.asyncio
async def test_fetch_has_independent_per_domain_limit(tmp_path):
    upstream_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(
            200,
            text="<html><title>Page</title><main>Page content</main></html>",
            headers={"content-type": "text/html"},
        )

    async with api_harness(handler, tmp_path, fetch_domain_qps=1) as (client, _):
        first = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/page"},
        )
        second = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/page"},
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "domain_rate_limit_exceeded"
    assert upstream_calls == 1
