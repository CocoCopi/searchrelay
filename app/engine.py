from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx

from app.content import html_to_text, sanitize_untrusted_text
from app.errors import APIError
from app.operators import ParsedQuery


@dataclass(frozen=True)
class SearchCandidate:
    title: str
    url: str
    snippet: str
    date: datetime | None = None
    engine_score: float | None = None
    source_engine: str | None = None
    language: str | None = None


class SearchBackend(Protocol):
    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> list[SearchCandidate]: ...

    async def healthcheck(self) -> bool: ...


class SearXNGBackend:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        upstream_url: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.client = client
        self.upstream_url = upstream_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> list[SearchCandidate]:
        query = _engine_query(parsed)
        candidates: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        seen_content: set[tuple[str, str]] = set()

        for page_number in range(1, 6):
            payload = await self._request_page(query, parsed, localization, page_number)
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise APIError(500, "The search backend returned an invalid response", "upstream_error")

            if not raw_results:
                break
            for raw in raw_results:
                if not isinstance(raw, dict):
                    continue
                candidate = _candidate_from_result(raw)
                if candidate is None:
                    continue
                normalized_url = _canonical_url(candidate.url)
                content_key = (candidate.title.casefold(), candidate.snippet.casefold())
                if normalized_url in seen_urls or content_key in seen_content:
                    continue
                seen_urls.add(normalized_url)
                seen_content.add(content_key)
                candidates.append(candidate)

            if len(candidates) >= count or len(raw_results) < 5:
                break

        return candidates

    async def healthcheck(self) -> bool:
        try:
            response = await self.client.get(
                f"{self.upstream_url}/healthz",
                timeout=min(self.timeout_seconds, 5.0),
            )
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300

    async def _request_page(
        self,
        query: str,
        parsed: ParsedQuery,
        localization: str,
        page_number: int,
    ) -> dict:
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "pageno": page_number,
            "safesearch": 0,
        }
        if parsed.languages:
            params["language"] = _language_code(parsed.languages[0])
        if localization:
            params["remote_ip"] = localization
        if parsed.after:
            params["time_range"] = _time_range(parsed.after)

        try:
            response = await self.client.get(
                f"{self.upstream_url}/search",
                params=params,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise APIError(504, "The search backend timed out", "downstream_timeout") from exc
        except httpx.RequestError as exc:
            raise APIError(504, "The search backend is unavailable", "downstream_timeout") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise APIError(504, "The search backend is unavailable", "downstream_timeout")
        if response.status_code >= 400:
            raise APIError(500, "The search backend rejected the request", "upstream_error")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise APIError(500, "The search backend returned invalid JSON", "upstream_error") from exc
        if not isinstance(payload, dict):
            raise APIError(500, "The search backend returned an invalid response", "upstream_error")
        return payload


def _engine_query(parsed: ParsedQuery) -> str:
    parts = [parsed.engine_query]
    if parsed.languages:
        parts.append(f"lang:{','.join(parsed.languages)}")
    if parsed.after:
        parts.append(f"after:{parsed.after.isoformat()}")
    if parsed.before:
        parts.append(f"before:{parsed.before.isoformat()}")
    return " ".join(part for part in parts if part).strip()


def _candidate_from_result(raw: dict) -> SearchCandidate | None:
    url = str(raw.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    title = sanitize_untrusted_text(str(raw.get("title") or ""))[:500]
    snippet_value = str(raw.get("content") or raw.get("snippet") or "")
    snippet = _clean_snippet(snippet_value)
    raw_score = raw.get("score")
    try:
        engine_score = float(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        engine_score = None
    source = raw.get("engine")
    language = raw.get("language") or raw.get("lang")
    return SearchCandidate(
        title=title,
        url=url,
        snippet=snippet,
        date=_parse_datetime(raw.get("publishedDate")),
        engine_score=engine_score,
        source_engine=str(source) if source is not None else None,
        language=str(language).strip().casefold() or None if language is not None else None,
    )


def _clean_snippet(value: str) -> str:
    value = value.strip()
    if "<" in value and ">" in value:
        value = html_to_text(value)
    return sanitize_untrusted_text(value).replace("\n", " ")[:2000]


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _language_code(language: str) -> str:
    normalized = language.casefold()
    if normalized == "zh":
        return "zh-CN"
    return normalized


def _time_range(after) -> str:
    from datetime import date

    now = date.today()
    age_days = (now - after).days
    if age_days <= 1:
        return "day"
    if age_days <= 31:
        return "month"
    if age_days <= 366:
        return "year"
    return ""


def _canonical_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, urlencode(query), "")
    )
