from __future__ import annotations

from typing import Callable

import httpx

from app.config import Settings
from app.engine import SearXNGBackend
from app.providers.adapters import (
    BraveSearchProvider,
    ExaProvider,
    OfficialApiConfig,
    SerperProvider,
    TavilyProvider,
)
from app.providers.config import ProviderRuntimeConfig, load_provider_configs
from app.providers.federation import FederatedSearchProvider
from app.providers.local import LocalIndexProvider
from app.providers.reliability import ReliabilityConfig, ResilientProvider


def build_search_provider(
    settings: Settings,
    client: httpx.AsyncClient,
    observer: Callable[[str, str, float], None] | None = None,
) -> FederatedSearchProvider:
    configs = load_provider_configs(settings)
    providers = []
    max_results: dict[str, int] = {}
    for provider_config in configs:
        raw_provider = _build_provider(provider_config, client)
        resilient = ResilientProvider(
            raw_provider,
            ReliabilityConfig(
                timeout_seconds=provider_config.timeout_seconds,
                retry_attempts=settings.provider_retry_attempts,
                retry_base_delay_seconds=settings.provider_retry_base_delay_seconds,
                circuit_failure_threshold=settings.provider_circuit_failure_threshold,
                circuit_reset_seconds=settings.provider_circuit_reset_seconds,
                requests_per_minute=provider_config.requests_per_minute,
                max_concurrency=provider_config.max_concurrency,
            ),
            observer=observer,
        )
        providers.append(resilient)
        max_results[provider_config.name] = provider_config.max_results
    return FederatedSearchProvider(
        providers,
        max_results_per_provider=max_results,
        rrf_k=settings.provider_rrf_k,
    )


def _build_provider(config: ProviderRuntimeConfig, client: httpx.AsyncClient):
    if config.name == "searxng":
        return SearXNGBackend(
            client=client,
            upstream_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
        )
    if config.name == "brave":
        return BraveSearchProvider(
            client,
            OfficialApiConfig(
                name="brave",
                api_key=config.api_key,
                base_url=config.base_url,
                timeout_seconds=config.timeout_seconds,
            ),
        )
    if config.name == "serper":
        return SerperProvider(
            client,
            OfficialApiConfig(
                name="serper",
                api_key=config.api_key,
                base_url=config.base_url,
                timeout_seconds=config.timeout_seconds,
            ),
        )
    if config.name == "tavily":
        return TavilyProvider(
            client,
            OfficialApiConfig(
                name="tavily",
                api_key=config.api_key,
                base_url=config.base_url,
                timeout_seconds=config.timeout_seconds,
            ),
        )
    if config.name == "exa":
        return ExaProvider(
            client,
            OfficialApiConfig(
                name="exa",
                api_key=config.api_key,
                base_url=config.base_url,
                timeout_seconds=config.timeout_seconds,
            ),
        )
    if config.name == "local":
        return LocalIndexProvider(config.base_url)
    raise RuntimeError(
        f"SearchRelay provider configuration error: provider '{config.name}' is not implemented"
    )
