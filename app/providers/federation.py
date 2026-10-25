from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Mapping, Sequence

from app.errors import APIError
from app.operators import ParsedQuery
from app.providers.base import (
    ProviderError,
    ProviderSearchResult,
    SearchCandidate,
    SearchProvider,
    canonical_url,
)


class FederatedSearchProvider:
    name = "federation"

    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        max_results_per_provider: Mapping[str, int] | None = None,
        rrf_k: int = 60,
    ) -> None:
        if not providers:
            raise ValueError("At least one search provider is required")
        self.providers = tuple(providers)
        self.max_results_per_provider = dict(max_results_per_provider or {})
        self.rrf_k = max(1, rrf_k)

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        responses = await asyncio.gather(
            *(
                provider.search(
                    parsed,
                    count=count,
                    localization=localization,
                )
                for provider in self.providers
            ),
            return_exceptions=True,
        )

        successful: list[ProviderSearchResult] = []
        failures: list[Exception] = []
        for response in responses:
            if isinstance(response, Exception):
                failures.append(response)
                continue
            successful.append(response)

        if not successful:
            provider_failures = [
                failure for failure in failures if isinstance(failure, ProviderError)
            ]
            invalid_response = any(
                failure.status_code == 500 for failure in provider_failures
            )
            raise APIError(
                500 if invalid_response else 504,
                (
                    "A configured search provider returned an invalid response"
                    if invalid_response
                    else "All configured search providers are unavailable"
                ),
                "upstream_error" if invalid_response else "downstream_timeout",
            )

        candidates = self._fuse(successful)
        providers_used = tuple(
            response.provider
            for response in sorted(
                successful,
                key=lambda item: self._provider_priority(item.provider),
            )
        )
        return ProviderSearchResult(
            provider=self.name,
            candidates=tuple(candidates),
            providers_used=providers_used,
        )

    async def healthcheck(self) -> bool:
        checks = await asyncio.gather(
            *(provider.healthcheck() for provider in self.providers),
            return_exceptions=True,
        )
        return any(check is True for check in checks)

    def _fuse(
        self,
        responses: list[ProviderSearchResult],
    ) -> list[SearchCandidate]:
        merged: dict[str, SearchCandidate] = {}
        contributions: dict[str, float] = {}
        provider_order: dict[str, int] = {}

        for response in responses:
            provider_order.setdefault(response.provider, len(provider_order))
            limit = max(1, self.max_results_per_provider.get(response.provider, count_of(response)))
            for rank, candidate in enumerate(response.candidates[:limit], start=1):
                key = canonical_url(candidate.url)
                contribution = 1.0 / (self.rrf_k + rank)
                contributions[key] = contributions.get(key, 0.0) + contribution
                if key not in merged:
                    merged[key] = replace(
                        candidate,
                        provider=response.provider,
                        providers=(response.provider,),
                        fusion_score=contribution,
                    )
                    continue
                existing = merged[key]
                providers = _unique_tuple((*existing.providers, candidate.provider))
                merged[key] = replace(
                    existing,
                    title=existing.title or candidate.title,
                    snippet=existing.snippet or candidate.snippet,
                    date=existing.date or candidate.date,
                    engine_score=(
                        candidate.engine_score
                        if existing.engine_score is None
                        else existing.engine_score
                    ),
                    language=existing.language or candidate.language,
                    providers=providers,
                )

        fused = [
            replace(candidate, fusion_score=contributions[canonical_url(candidate.url)])
            for candidate in merged.values()
        ]
        fused.sort(
            key=lambda candidate: (
                -candidate.fusion_score,
                provider_order.get(candidate.provider, len(provider_order)),
                candidate.url.casefold(),
            )
        )
        return fused

    def _provider_priority(self, provider: str) -> int:
        for index, configured in enumerate(self.providers):
            if configured.name == provider:
                return index
        return len(self.providers)


def count_of(response: ProviderSearchResult) -> int:
    return max(1, len(response.candidates))


def _unique_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
