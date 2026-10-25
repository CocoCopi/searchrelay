from __future__ import annotations

import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


class APIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str = "",
        error_type: str = "invalid_request_error",
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.error_type = error_type
        self.param = param


@dataclass(frozen=True)
class SearchResult:
    rerank_score: float
    title: str
    url: str
    snippet: str
    summary: str
    content: str
    site_name: str | None
    site_icon: str | None
    date: str | None
    authority: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SearchResult":
        return cls(
            rerank_score=float(payload.get("rerank_score", 0)),
            title=str(payload.get("title", "")),
            url=str(payload.get("url", "")),
            snippet=str(payload.get("snippet", "")),
            summary=str(payload.get("summary", "")),
            content=str(payload.get("content", "")),
            site_name=payload.get("site_name"),
            site_icon=payload.get("site_icon"),
            date=payload.get("date"),
            authority=int(payload.get("authority", -1)),
        )


@dataclass(frozen=True)
class FetchPage:
    title: str
    content: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FetchPage":
        return cls(
            title=str(payload.get("title", "")),
            content=str(payload.get("content", "")),
        )


class SearchRelayClient:
    """Synchronous client for the SearchRelay."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max(0, max_retries)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def search(
        self,
        query: str,
        *,
        count: int = 10,
        ori_question: str | None = None,
        localization: str = "",
        need_content: bool = False,
    ) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "query": query,
            "count": count,
            "localization": localization,
            "need_content": need_content,
        }
        if ori_question is not None:
            payload["ori_question"] = ori_question
        response = self._post("/v1/tools/search", payload)
        data = _json_object(response)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise APIError(
                "The API returned an invalid search response",
                status_code=response.status_code,
                code="invalid_response",
            )
        return [SearchResult.from_dict(item) for item in results if isinstance(item, dict)]

    def fetch(self, url: str) -> FetchPage:
        response = self._post("/v1/tools/fetch", {"url": url})
        return FetchPage.from_dict(_json_object(response))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SearchRelayClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(path, json=payload)
            except httpx.TransportError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                _sleep_before_retry(attempt, None)
                continue

            if response.status_code in {408, 429} or 500 <= response.status_code < 600:
                if attempt < self.max_retries:
                    _sleep_before_retry(attempt, response.headers.get("retry-after"))
                    continue
            _raise_for_error(response)
            return response

        raise APIError(
            str(last_error or "Request failed"),
            status_code=0,
            code="transport_error",
        )


def _raise_for_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = f"Search/Fetch request failed with HTTP {response.status_code}"
    code = "http_error"
    error_type = "invalid_request_error"
    param: str | None = None
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            message = str(error.get("message") or message)
            code = str(error.get("code") or code)
            error_type = str(error.get("type") or error_type)
            raw_param = error.get("param")
            param = str(raw_param) if raw_param is not None else None
    except ValueError:
        pass
    raise APIError(
        message,
        status_code=response.status_code,
        code=code,
        error_type=error_type,
        param=param,
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise APIError(
            "The API returned invalid JSON",
            status_code=response.status_code,
            code="invalid_response",
        ) from exc
    if not isinstance(payload, dict):
        raise APIError(
            "The API returned an invalid response object",
            status_code=response.status_code,
            code="invalid_response",
        )
    return payload


def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
    delay = min(8.0, 0.5 * (2**attempt))
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after).timestamp()
                delay = max(delay, retry_at - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
    time.sleep(min(delay, 30.0) + random.uniform(0, 0.1))
