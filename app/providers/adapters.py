from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.content import html_to_text, sanitize_untrusted_text
from app.operators import ParsedQuery
from app.providers.base import (
    ProviderError,
    ProviderSearchResult,
    SearchCandidate,
    parse_provider_datetime,
)


@dataclass(frozen=True)
class OfficialApiConfig:
    name: str
    api_key: str
    base_url: str
    timeout_seconds: float = 5.0


class OfficialApiProvider:
    name = "official-api"

    def __init__(self, client: httpx.AsyncClient, config: OfficialApiConfig) -> None:
        self.client = client
        self.config = config
        self.name = config.name

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        raise NotImplementedError

    async def healthcheck(self) -> bool:
        try:
            await self._request_json(
                "GET",
                self.health_url(),
                headers=self.health_headers(),
                params={"q": "searchrelay health", "count": 1},
            )
        except ProviderError:
            return False
        return True

    def health_url(self) -> str:
        raise NotImplementedError

    def health_headers(self) -> dict[str, str]:
        return self.request_headers()

    def request_headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self.client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=self.config.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Provider {self.name} timed out",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"Provider {self.name} is unavailable",
                retryable=True,
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(
                f"Provider {self.name} returned HTTP {response.status_code}",
                retryable=True,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Provider {self.name} rejected the request",
                retryable=False,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Provider {self.name} returned invalid JSON",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError(
                f"Provider {self.name} returned an invalid response",
                retryable=True,
            )
        return payload


class BraveSearchProvider(OfficialApiProvider):
    def __init__(self, client: httpx.AsyncClient, config: OfficialApiConfig) -> None:
        super().__init__(client, config)
        self.name = "brave"

    def health_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/web/search"

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Subscription-Token": self.config.api_key,
        }

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        params: dict[str, Any] = {
            "q": _provider_query(parsed),
            "count": min(max(1, count), 20),
        }
        if parsed.languages:
            params["search_lang"] = parsed.languages[0]
        payload = await self._request_json(
            "GET",
            f"{self.config.base_url.rstrip('/')}/web/search",
            headers=self.request_headers(),
            params=params,
        )
        web = payload.get("web")
        raw_results = web.get("results", []) if isinstance(web, dict) else []
        candidates = tuple(
            candidate
            for raw in _result_list(raw_results)
            if (candidate := self._candidate(raw)) is not None
        )
        return ProviderSearchResult(
            provider=self.name,
            candidates=candidates,
            providers_used=(self.name,),
        )

    def _candidate(self, raw: dict[str, Any]) -> SearchCandidate | None:
        url = str(raw.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        return SearchCandidate(
            title=_clean_text(raw.get("title"), 500),
            url=url,
            snippet=_clean_text(raw.get("description"), 2000),
            date=parse_provider_datetime(raw.get("page_age") or raw.get("publishedDate")),
            engine_score=_float(raw.get("score")),
            source_engine=self.name,
            language=_optional_text(raw.get("language")),
            provider=self.name,
            providers=(self.name,),
        )


class SerperProvider(OfficialApiProvider):
    def __init__(self, client: httpx.AsyncClient, config: OfficialApiConfig) -> None:
        super().__init__(client, config)
        self.name = "serper"

    def health_url(self) -> str:
        return self.config.base_url.rstrip("/")

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-KEY": self.config.api_key,
        }

    async def healthcheck(self) -> bool:
        try:
            await self._request_json(
                "POST",
                self.config.base_url.rstrip("/"),
                headers=self.request_headers(),
                json={"q": "searchrelay health", "num": 1},
            )
        except ProviderError:
            return False
        return True

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        payload = await self._request_json(
            "POST",
            self.config.base_url.rstrip("/"),
            headers=self.request_headers(),
            json={
                "q": _provider_query(parsed),
                "num": min(max(1, count), 100),
            },
        )
        candidates = tuple(
            candidate
            for raw in _result_list(payload.get("organic"))
            if (candidate := self._candidate(raw)) is not None
        )
        return ProviderSearchResult(
            provider=self.name,
            candidates=candidates,
            providers_used=(self.name,),
        )

    def _candidate(self, raw: dict[str, Any]) -> SearchCandidate | None:
        url = str(raw.get("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        return SearchCandidate(
            title=_clean_text(raw.get("title"), 500),
            url=url,
            snippet=_clean_text(raw.get("snippet"), 2000),
            date=parse_provider_datetime(raw.get("date")),
            engine_score=_float(raw.get("score")),
            source_engine=self.name,
            language=_optional_text(raw.get("language")),
            provider=self.name,
            providers=(self.name,),
        )


class TavilyProvider(OfficialApiProvider):
    def __init__(self, client: httpx.AsyncClient, config: OfficialApiConfig) -> None:
        super().__init__(client, config)
        self.name = "tavily"

    def health_url(self) -> str:
        return self.config.base_url.rstrip("/")

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

    async def healthcheck(self) -> bool:
        try:
            await self._request_json(
                "POST",
                self.config.base_url.rstrip("/"),
                headers=self.request_headers(),
                json={
                    "query": "searchrelay health",
                    "max_results": 1,
                    "search_depth": "basic",
                },
            )
        except ProviderError:
            return False
        return True

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        payload = await self._request_json(
            "POST",
            self.config.base_url.rstrip("/"),
            headers=self.request_headers(),
            json={
                "query": _provider_query(parsed),
                "max_results": min(max(1, count), 20),
                "search_depth": "basic",
                "include_answer": False,
            },
        )
        candidates = tuple(
            candidate
            for raw in _result_list(payload.get("results"))
            if (candidate := self._candidate(raw)) is not None
        )
        return ProviderSearchResult(
            provider=self.name,
            candidates=candidates,
            providers_used=(self.name,),
        )

    def _candidate(self, raw: dict[str, Any]) -> SearchCandidate | None:
        url = str(raw.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        return SearchCandidate(
            title=_clean_text(raw.get("title"), 500),
            url=url,
            snippet=_clean_text(raw.get("content"), 2000),
            date=parse_provider_datetime(raw.get("published_date")),
            engine_score=_float(raw.get("score")),
            source_engine=self.name,
            language=_optional_text(raw.get("language")),
            provider=self.name,
            providers=(self.name,),
        )


class ExaProvider(OfficialApiProvider):
    def __init__(self, client: httpx.AsyncClient, config: OfficialApiConfig) -> None:
        super().__init__(client, config)
        self.name = "exa"

    def health_url(self) -> str:
        return self.config.base_url.rstrip("/")

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
        }

    async def healthcheck(self) -> bool:
        try:
            await self._request_json(
                "POST",
                self.config.base_url.rstrip("/"),
                headers=self.request_headers(),
                json={
                    "query": "searchrelay health",
                    "numResults": 1,
                    "type": "auto",
                },
            )
        except ProviderError:
            return False
        return True

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        payload = await self._request_json(
            "POST",
            self.config.base_url.rstrip("/"),
            headers=self.request_headers(),
            json={
                "query": _provider_query(parsed),
                "numResults": min(max(1, count), 100),
                "type": "auto",
                "contents": {"text": {"maxCharacters": 2000}},
            },
        )
        candidates = tuple(
            candidate
            for raw in _result_list(payload.get("results"))
            if (candidate := self._candidate(raw)) is not None
        )
        return ProviderSearchResult(
            provider=self.name,
            candidates=candidates,
            providers_used=(self.name,),
        )

    def _candidate(self, raw: dict[str, Any]) -> SearchCandidate | None:
        url = str(raw.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        text = raw.get("text")
        snippet = text.get("text") if isinstance(text, dict) else raw.get("snippet")
        return SearchCandidate(
            title=_clean_text(raw.get("title"), 500),
            url=url,
            snippet=_clean_text(snippet, 2000),
            date=parse_provider_datetime(raw.get("publishedDate")),
            engine_score=_float(raw.get("score")),
            source_engine=self.name,
            language=_optional_text(raw.get("language")),
            provider=self.name,
            providers=(self.name,),
        )


def _provider_query(parsed: ParsedQuery) -> str:
    parts = [parsed.engine_query]
    if parsed.languages:
        parts.append(f"lang:{','.join(parsed.languages)}")
    if parsed.after:
        parts.append(f"after:{parsed.after.isoformat()}")
    if parsed.before:
        parts.append(f"before:{parsed.before.isoformat()}")
    return " ".join(part for part in parts if part).strip()


def _result_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _clean_text(value: object, maximum: int) -> str:
    text = str(value or "").strip()
    if "<" in text and ">" in text:
        text = html_to_text(text)
    return sanitize_untrusted_text(text).replace("\n", " ")[:maximum]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    return text or None


def _float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
