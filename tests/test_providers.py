from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.errors import APIError
from app.operators import parse_query
from app.providers.adapters import (
    BraveSearchProvider,
    ExaProvider,
    OfficialApiConfig,
    SerperProvider,
    TavilyProvider,
)
from app.providers.base import ProviderError, ProviderSearchResult, SearchCandidate
from app.providers.config import load_provider_configs
from app.providers.federation import FederatedSearchProvider
from app.providers.reliability import ReliabilityConfig, ResilientProvider
from tests.helpers import JSON_HEADERS, api_harness


class StubProvider:
    def __init__(
        self,
        name: str,
        candidates: tuple[SearchCandidate, ...] = (),
        *,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.name = name
        self.candidates = candidates
        self.error = error
        self.delay = delay
        self.calls = 0

    async def search(self, parsed, *, count: int, localization: str) -> ProviderSearchResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return ProviderSearchResult(
            provider=self.name,
            candidates=self.candidates,
            providers_used=(self.name,),
        )

    async def healthcheck(self) -> bool:
        return self.error is None


def candidate(url: str, title: str, provider: str = "test") -> SearchCandidate:
    return SearchCandidate(
        title=title,
        url=url,
        snippet=f"Verified snippet for {title}",
        provider=provider,
        providers=(provider,),
    )


@pytest.mark.asyncio
async def test_brave_adapter_uses_official_api_and_parses_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["token"] = request.headers.get("x-subscription-token")
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Brave result",
                            "url": "https://example.com/brave",
                            "description": "Official Brave API description.",
                            "page_age": "2025-01-02",
                            "language": "en",
                        }
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BraveSearchProvider(
            client,
            OfficialApiConfig(
                name="brave",
                api_key="brave-secret",
                base_url="https://api.search.brave.com/res/v1",
            ),
        )
        result = await provider.search(parse_query("topic"), count=3, localization="")

    assert str(captured["url"]).startswith("https://api.search.brave.com/res/v1/web/search")
    assert captured["token"] == "brave-secret"
    assert result.providers_used == ("brave",)
    assert result.candidates[0].title == "Brave result"
    assert result.candidates[0].date is not None


@pytest.mark.asyncio
async def test_serper_adapter_uses_official_api_and_parses_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Serper result",
                        "link": "https://example.com/serper",
                        "snippet": "Official Serper API snippet.",
                        "date": "Jan 2, 2025",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SerperProvider(
            client,
            OfficialApiConfig(
                name="serper",
                api_key="serper-secret",
                base_url="https://google.serper.dev/search",
            ),
        )
        result = await provider.search(parse_query("topic"), count=3, localization="")

    assert captured["url"] == "https://google.serper.dev/search"
    assert captured["key"] == "serper-secret"
    assert captured["payload"] == {"q": "topic", "num": 3}
    assert result.candidates[0].title == "Serper result"


@pytest.mark.asyncio
async def test_tavily_adapter_uses_official_api_and_parses_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Tavily result",
                        "url": "https://example.com/tavily",
                        "content": "Official Tavily API content.",
                        "published_date": "2025-01-02",
                        "score": 0.9,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TavilyProvider(
            client,
            OfficialApiConfig(
                name="tavily",
                api_key="tavily-secret",
                base_url="https://api.tavily.com/search",
            ),
        )
        result = await provider.search(parse_query("topic"), count=3, localization="")

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["authorization"] == "Bearer tavily-secret"
    assert captured["payload"]["query"] == "topic"
    assert result.candidates[0].engine_score == 0.9


@pytest.mark.asyncio
async def test_exa_adapter_uses_official_api_and_parses_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Exa result",
                        "url": "https://example.com/exa",
                        "text": {"text": "Official Exa API text."},
                        "publishedDate": "2025-01-02",
                        "score": 0.8,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ExaProvider(
            client,
            OfficialApiConfig(
                name="exa",
                api_key="exa-secret",
                base_url="https://api.exa.ai/search",
            ),
        )
        result = await provider.search(parse_query("topic"), count=3, localization="")

    assert captured["url"] == "https://api.exa.ai/search"
    assert captured["key"] == "exa-secret"
    assert captured["payload"]["query"] == "topic"
    assert result.candidates[0].snippet == "Official Exa API text."


@pytest.mark.asyncio
async def test_federation_uses_rrf_and_deduplicates_canonical_urls() -> None:
    shared = candidate("https://example.com/article?utm_source=test", "Shared")
    first = StubProvider(
        "first",
        (
            shared,
            candidate("https://one.example/result", "One", "first"),
        ),
    )
    second = StubProvider(
        "second",
        (
            candidate("https://example.com/article", "Shared duplicate", "second"),
            candidate("https://two.example/result", "Two", "second"),
        ),
    )
    federation = FederatedSearchProvider((first, second))

    result = await federation.search(parse_query("topic"), count=10, localization="")

    assert len(result.candidates) == 3
    assert result.candidates[0].url.startswith("https://example.com/article")
    assert result.candidates[0].providers == ("first", "second")
    assert result.providers_used == ("first", "second")
    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_federation_keeps_successful_provider_when_another_fails() -> None:
    healthy = StubProvider("healthy", (candidate("https://ok.example/result", "OK"),))
    broken = StubProvider("broken", error=ProviderError("offline"))
    federation = FederatedSearchProvider((broken, healthy))

    result = await federation.search(parse_query("topic"), count=10, localization="")

    assert [item.url for item in result.candidates] == ["https://ok.example/result"]
    assert result.providers_used == ("healthy",)


@pytest.mark.asyncio
async def test_federation_returns_504_when_every_provider_fails() -> None:
    federation = FederatedSearchProvider(
        (
            StubProvider("one", error=ProviderError("offline")),
            StubProvider("two", error=ProviderError("offline")),
        )
    )

    with pytest.raises(APIError) as captured:
        await federation.search(parse_query("topic"), count=10, localization="")

    assert captured.value.status_code == 504
    assert captured.value.code == "downstream_timeout"


@pytest.mark.asyncio
async def test_federation_queries_providers_in_parallel() -> None:
    release = asyncio.Event()
    started = [asyncio.Event(), asyncio.Event()]

    class BarrierProvider(StubProvider):
        def __init__(self, name: str, event: asyncio.Event) -> None:
            super().__init__(name)
            self.event = event

        async def search(self, parsed, *, count: int, localization: str):
            self.calls += 1
            self.event.set()
            await release.wait()
            return ProviderSearchResult(self.name, (), (self.name,))

    federation = FederatedSearchProvider(
        (
            BarrierProvider("one", started[0]),
            BarrierProvider("two", started[1]),
        )
    )
    task = asyncio.create_task(
        federation.search(parse_query("topic"), count=10, localization="")
    )
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=0.5)
    release.set()
    await task


@pytest.mark.asyncio
async def test_resilient_provider_retries_and_opens_circuit() -> None:
    class BrokenProvider(StubProvider):
        async def search(self, parsed, *, count: int, localization: str):
            self.calls += 1
            raise ProviderError("temporary failure", retryable=True)

    provider = BrokenProvider("broken")
    resilient = ResilientProvider(
        provider,
        ReliabilityConfig(
            timeout_seconds=1.0,
            retry_attempts=2,
            retry_base_delay_seconds=0.0,
            circuit_failure_threshold=2,
            circuit_reset_seconds=60.0,
            requests_per_minute=60,
            max_concurrency=1,
        ),
    )

    with pytest.raises(ProviderError):
        await resilient.search(parse_query("topic"), count=1, localization="")
    with pytest.raises(ProviderError):
        await resilient.search(parse_query("topic"), count=1, localization="")
    with pytest.raises(ProviderError):
        await resilient.search(parse_query("topic"), count=1, localization="")

    assert provider.calls == 4


@pytest.mark.asyncio
async def test_resilient_provider_enforces_per_provider_timeout() -> None:
    class SlowProvider(StubProvider):
        async def search(self, parsed, *, count: int, localization: str):
            self.calls += 1
            await asyncio.sleep(0.2)
            return ProviderSearchResult(self.name, (), (self.name,))

    provider = SlowProvider("slow")
    resilient = ResilientProvider(
        provider,
        ReliabilityConfig(
            timeout_seconds=0.01,
            retry_attempts=1,
            retry_base_delay_seconds=0.0,
            circuit_failure_threshold=5,
            circuit_reset_seconds=60.0,
            requests_per_minute=60,
            max_concurrency=1,
        ),
    )

    with pytest.raises(ProviderError, match="timed out"):
        await resilient.search(parse_query("topic"), count=1, localization="")

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_provider_request_budget_prevents_extra_upstream_calls() -> None:
    provider = StubProvider("budgeted")
    resilient = ResilientProvider(
        provider,
        ReliabilityConfig(
            timeout_seconds=1.0,
            retry_attempts=1,
            retry_base_delay_seconds=0.0,
            circuit_failure_threshold=5,
            circuit_reset_seconds=60.0,
            requests_per_minute=1,
            max_concurrency=1,
        ),
    )

    await resilient.search(parse_query("topic"), count=1, localization="")
    with pytest.raises(ProviderError, match="budget exhausted"):
        await resilient.search(parse_query("topic"), count=1, localization="")

    assert provider.calls == 1


def test_provider_configuration_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings(provider_priorities_csv="brave")

    with pytest.raises(RuntimeError, match="BRAVE_SEARCH_API_KEY"):
        load_provider_configs(settings)


def test_provider_configuration_loads_priorities_and_budgets(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "priorities": ["brave", "serper"],
                "providers": {
                    "brave": {
                        "enabled": True,
                        "api_key_env": "TEST_BRAVE_KEY",
                        "timeout_seconds": 2.5,
                        "requests_per_minute": 17,
                        "max_concurrency": 3,
                        "max_results": 11,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_BRAVE_KEY", "secret")
    monkeypatch.setenv("SERPER_API_KEY", "serper-secret")
    configs = load_provider_configs(
        Settings(provider_config_file=config_path)
    )

    assert [config.name for config in configs] == ["brave", "serper"]
    assert configs[0].requests_per_minute == 17
    assert configs[0].max_concurrency == 3
    assert configs[0].max_results == 11
    assert configs[0].api_key == "secret"


@pytest.mark.asyncio
async def test_search_header_lists_only_successful_providers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/res/v1/web/search":
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Brave fallback",
                                "url": "https://example.com/fallback",
                                "description": "Verified Brave fallback result with enough detail. "
                                * 20,
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/search":
            return httpx.Response(503)
        return httpx.Response(200, text="OK")

    async with api_harness(
        handler,
        tmp_path,
        provider_priorities_csv="searxng,brave",
        provider_retry_attempts=1,
        provider_retry_base_delay_seconds=0.0,
    ) as (client, _):
        response = await client.post(
            "/v1/tools/search",
            headers=JSON_HEADERS,
            json={"query": "fallback", "count": 1},
        )

    assert response.status_code == 200
    assert response.headers["X-Providers-Used"] == "brave"
    assert response.json()["results"][0]["title"] == "Brave fallback"
