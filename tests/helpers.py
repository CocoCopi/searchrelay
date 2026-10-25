from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx

from app.config import Settings
from app.main import create_app


Handler = Callable[[httpx.Request], httpx.Response]


@asynccontextmanager
async def api_harness(
    handler: Handler,
    tmp_path: Path,
    **setting_overrides: Any,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    settings_values: dict[str, Any] = {
        "api_keys_json": {"sk-test": "account-test"},
        "initial_balances_json": {"account-test": 1_000_000},
        "database_path": tmp_path / "billing.db",
        "upstream_url": "http://search.test",
        "respect_robots": False,
        "search_qps": 100,
        "fetch_qps": 25,
        "fetch_domain_qps": 100,
        "fetch_max_bytes": 1024 * 1024,
        "max_request_body_bytes": 64 * 1024,
    }
    settings_values.update(setting_overrides)
    settings = Settings(**settings_values)

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    application = create_app(settings, http_client=upstream_client)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
            yield client, application


def searxng_response(
    results: list[dict[str, Any]] | None = None,
    *,
    status_code: int = 200,
) -> httpx.Response:
    payload = {"results": results if results is not None else []}
    return httpx.Response(status_code=status_code, json=payload)


def search_result(
    *,
    title: str = "Nature research result",
    url: str = "https://www.nature.com/articles/example",
    content: str | None = None,
    published_date: str | None = "2026-10-20T12:00:00Z",
    language: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": title,
        "url": url,
        "content": content
        or (
            "This source explains the requested topic with reliable evidence and useful context. "
            * 8
        ),
        "engine": "bing",
    }
    if published_date is not None:
        result["publishedDate"] = published_date
    if language is not None:
        result["language"] = language
    return result


AUTH_HEADERS = {"Authorization": "Bearer sk-test"}
JSON_HEADERS = {"Authorization": "Bearer sk-test", "Content-Type": "application/json"}
