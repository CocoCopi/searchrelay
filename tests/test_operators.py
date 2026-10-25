from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.operators import parse_query
from tests.helpers import JSON_HEADERS, api_harness, search_result, searxng_response


def test_combined_operators_are_parsed() -> None:
    parsed = parse_query(
        'topic -spam "exact phrase" site:a.com -site:bad.com lang:zh,en '
        "after:2024 before:2025-03"
    )

    assert parsed.terms == ("topic",)
    assert parsed.phrases == ("exact phrase",)
    assert parsed.excluded_terms == ("spam",)
    assert parsed.positive_sites == ("a.com",)
    assert parsed.negative_sites == ("bad.com",)
    assert parsed.languages == ("zh", "en")
    assert parsed.after == date(2024, 1, 1)
    assert parsed.before == date(2025, 3, 1)


def test_multiple_site_group_is_parsed() -> None:
    parsed = parse_query('"site:a.com OR site:b.com" topic')

    assert parsed.positive_sites == ("a.com", "b.com")
    assert parsed.phrases == ()
    assert "site:a.com OR site:b.com" in parsed.engine_query

    unquoted = parse_query("site:a.com OR site:b.com topic")
    assert unquoted.positive_sites == ("a.com", "b.com")
    assert unquoted.terms == ("topic",)


@pytest.mark.asyncio
async def test_operators_are_forwarded_to_search_backend(tmp_path):
    captured_query = ""
    captured_language = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query, captured_language
        captured_query = request.url.params.get("q", "")
        captured_language = request.url.params.get("language", "")
        return searxng_response([])

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": 'topic -spam "exact phrase" site:a.com lang:zh after:2024'},
        )

    assert response.status_code == 200
    assert "topic" in captured_query
    assert "-spam" in captured_query
    assert '"exact phrase"' in captured_query
    assert "site:a.com" in captured_query
    assert "lang:zh" in captured_query
    assert "after:2024-01-01" in captured_query
    assert captured_language == "zh-CN"


@pytest.mark.asyncio
async def test_date_operator_filters_known_result_dates(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return searxng_response(
            [
                search_result(
                    title="Old result",
                    url="https://www.nature.com/articles/old",
                    published_date="2020-01-01T00:00:00Z",
                ),
                search_result(
                    title="New result",
                    url="https://www.nature.com/articles/new",
                    published_date="2025-01-01T00:00:00Z",
                ),
            ]
        )

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "topic after:2024", "count": 10},
        )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["title"] == "New result"


@pytest.mark.asyncio
async def test_invalid_date_operator_returns_400(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return searxng_response([])

    async with api_harness(handler, tmp_path) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "topic after:not-a-date"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query"
