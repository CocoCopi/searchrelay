from __future__ import annotations

import socket
import time

import httpx
import pytest

from app.local_index.crawler import CrawlConfig, Crawler, _read_seeds
from app.local_index.store import LocalIndex, build_match_query
from app.operators import parse_query
from app.providers.local import LocalIndexProvider
from tests.helpers import JSON_HEADERS, api_harness


def test_local_index_stores_and_searches_documents(tmp_path) -> None:
    index = LocalIndex(tmp_path / "local.db")
    index.upsert(
        url="https://docs.example.com/guide",
        title="Deployment guide",
        content="Run the service with docker compose and read the logs.",
    )
    index.upsert(
        url="https://docs.example.com/api",
        title="API reference",
        content="The search endpoint accepts a query and returns results.",
    )

    documents = index.search("deployment guide")

    assert documents
    assert documents[0].url == "https://docs.example.com/guide"
    assert index.count() == 2


def test_local_index_update_replaces_content(tmp_path) -> None:
    index = LocalIndex(tmp_path / "local.db")
    index.upsert(url="https://docs.example.com/page", title="Old", content="old content")
    index.upsert(url="https://docs.example.com/page", title="New", content="new content")

    documents = index.search("content")

    assert index.count() == 1
    assert documents[0].title == "New"


def test_match_query_quotes_user_input() -> None:
    match_query = build_match_query('python "exact phrase" OR NEAR(x)')

    assert '"python"' in match_query
    assert '"exact phrase"' in match_query
    assert "OR" not in match_query.replace('"OR"', "")


@pytest.mark.asyncio
async def test_local_provider_returns_candidates(tmp_path) -> None:
    index = LocalIndex(tmp_path / "local.db")
    index.upsert(
        url="https://docs.example.com/guide",
        title="Deployment guide",
        content="Run the service with docker compose and read the logs.",
    )

    provider = LocalIndexProvider(tmp_path / "local.db")
    result = await provider.search(parse_query("deployment guide"), count=5, localization="")

    assert result.provider == "local"
    assert result.providers_used == ("local",)
    assert result.candidates[0].url == "https://docs.example.com/guide"


@pytest.mark.asyncio
async def test_search_can_use_the_local_provider(tmp_path) -> None:
    index = LocalIndex(tmp_path / "local.db")
    index.upsert(
        url="https://docs.example.com/guide",
        title="Deployment guide",
        content="Run the service with docker compose and read the logs. " * 5,
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with api_harness(
        handler,
        tmp_path,
        provider_priorities="local",
        local_index_path=tmp_path / "local.db",
    ) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "deployment guide", "count": 3},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["url"] == "https://docs.example.com/guide"
    assert response.headers["X-Providers-Used"] == "local"


@pytest.mark.asyncio
async def test_crawler_indexes_seed_pages_and_obeys_robots(tmp_path, monkeypatch) -> None:
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private\n",
                headers={"content-type": "text/plain"},
            )
        if request.url.path == "/private/secret":
            return httpx.Response(200, text="<html><main>secret</main></html>")
        if request.url.path == "/guide":
            return httpx.Response(
                200,
                text=(
                    "<html><body><main><h1>Guide</h1>"
                    "<p>The deployment guide explains how to run the service.</p>"
                    "<a href='/other'>other</a></main></body></html>"
                ),
                headers={"content-type": "text/html"},
            )
        if request.url.path == "/other":
            return httpx.Response(
                200,
                text="<html><body><main><p>Another page with useful content.</p></main></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404, text="missing")

    index = LocalIndex(tmp_path / "local.db")
    config = CrawlConfig(
        seeds=["https://docs.example.com/guide"],
        max_pages=5,
        max_depth=1,
        delay_seconds=0.0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        crawler = Crawler(config=config, index=index, client=client)
        stats = await crawler.run()

    assert stats.indexed == 2
    assert index.count() == 2
    assert index.hosts() == ["docs.example.com"]
    assert any(document.url.endswith("/guide") for document in index.search("deployment"))


@pytest.mark.asyncio
async def test_crawler_skips_robots_denied_pages(tmp_path, monkeypatch) -> None:
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text="<html><main>secret</main></html>")

    index = LocalIndex(tmp_path / "local.db")
    config = CrawlConfig(seeds=["https://docs.example.com/page"], delay_seconds=0.0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        crawler = Crawler(config=config, index=index, client=client)
        stats = await crawler.run()

    assert stats.robots_denied == 1
    assert stats.indexed == 0
    assert index.count() == 0


@pytest.mark.asyncio
async def test_crawler_waits_between_requests_to_the_same_host(tmp_path, monkeypatch) -> None:
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        return httpx.Response(
            200,
            text="<html><main><p>Content for politeness testing.</p></main></html>",
            headers={"content-type": "text/html"},
        )

    index = LocalIndex(tmp_path / "local.db")
    config = CrawlConfig(
        seeds=["https://docs.example.com/a", "https://docs.example.com/b"],
        delay_seconds=0.25,
        max_pages=2,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        crawler = Crawler(config=config, index=index, client=client)
        started = time.monotonic()
        await crawler.run()
        elapsed = time.monotonic() - started

    assert elapsed >= 0.25


def test_read_seeds_normalizes_domains(tmp_path) -> None:
    seed_file = tmp_path / "seeds.txt"
    seed_file.write_text("# comment\nexample.com\nhttps://docs.example.com\n", encoding="utf-8")

    seeds = _read_seeds(str(seed_file), ["additional.example"])

    assert seeds == [
        "https://additional.example",
        "https://example.com",
        "https://docs.example.com",
    ]
