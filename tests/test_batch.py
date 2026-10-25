from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.helpers import JSON_HEADERS, api_harness, search_result


@pytest.mark.asyncio
async def test_warm_cache_avoids_second_upstream_search(tmp_path) -> None:
    calls: dict[str, int] = {"search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            calls["search"] += 1
            return httpx.Response(200, json={"results": [search_result()]})
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path) as (client, _):
        first = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "cached query", "count": 1},
        )
        second = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "cached query", "count": 1},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert calls["search"] == 1


@pytest.mark.asyncio
async def test_concurrent_identical_searches_share_one_upstream_call(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/search":
            calls += 1
            return httpx.Response(200, json={"results": [search_result()]})
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path) as (client, _):
        responses = await asyncio.gather(
            *(
                client.post(
                    "/v1/tools/search",
                    headers=JSON_HEADERS,
                    json={"query": "coalesced query", "count": 1},
                )
                for _ in range(4)
            )
        )

    assert {response.status_code for response in responses} == {200}
    assert calls == 1


@pytest.mark.asyncio
async def test_fetch_cache_revalidates_with_etag(tmp_path) -> None:
    state = {"requests": 0, "conditional_seen": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            state["requests"] += 1
            if request.headers.get("if-none-match") == '"v1"':
                state["conditional_seen"] = True
                return httpx.Response(304, headers={"ETag": '"v1"'})
            return httpx.Response(
                200,
                headers={"ETag": '"v1"'},
                text="<html><body><article><h1>Page</h1><p>Real body content.</p></article></body></html>",
            )
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path, cache_fetch_ttl_seconds=0.0) as (client, _):
        first = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/article"},
        )
        second = await client.post(
            "/v1/tools/fetch",
            headers=JSON_HEADERS,
            json={"url": "https://93.184.216.34/article"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert state["requests"] == 2
    assert state["conditional_seen"] is True


@pytest.mark.asyncio
async def test_batch_search_bills_once_per_successful_query(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [search_result()]})
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/search/batch",
            headers=JSON_HEADERS,
            json={"queries": ["first query", "second query"], "count": 1},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["results"]
    assert body["items"][1]["results"]
    assert response.headers["X-Providers-Used"] == "searxng"
    assert before - after == 2 * 750


@pytest.mark.asyncio
async def test_batch_search_does_not_bill_failed_queries(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path) as (client, application):
        before = application.state.billing.get_balance("account-test")
        response = await client.post(
            "/v1/tools/search/batch",
            headers=JSON_HEADERS,
            json={"queries": ["no match"], "count": 1},
        )
        after = application.state.billing.get_balance("account-test")

    assert response.status_code == 200
    assert response.json()["items"][0]["results"] == []
    assert before == after


@pytest.mark.asyncio
async def test_batch_search_rejects_more_than_ten_queries(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/search/batch",
            headers=JSON_HEADERS,
            json={"queries": [f"query {index}" for index in range(11)]},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_batch_search_reports_upstream_failure(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(503)
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path, provider_retry_attempts=1) as (client, _):
        response = await client.post(
            "/v1/tools/search/batch",
            headers=JSON_HEADERS,
            json={"queries": ["broken"], "count": 1},
        )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "downstream_timeout"


@pytest.mark.asyncio
async def test_batch_search_keeps_partial_success(tmp_path) -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(200, json={"results": [search_result()]})
            return httpx.Response(503)
        return httpx.Response(200, text="OK")

    async with api_harness(handler, tmp_path, provider_retry_attempts=1) as (client, _):
        response = await client.post(
            "/v1/tools/search/batch",
            headers=JSON_HEADERS,
            json={"queries": ["good", "bad"], "count": 1},
        )

    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["results"]
    assert items[1]["results"] == []
    assert items[1]["error"]["code"] == "downstream_timeout"
