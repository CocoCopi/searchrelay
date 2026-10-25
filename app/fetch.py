from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.cache import CacheEntry, SingleFlight, TwoTierCache
from app.content import ExtractedPage, extract_page, sanitize_untrusted_text
from app.errors import APIError
from app.security import (
    SecurityPolicy,
    URLValidationError,
    ValidatedURL,
    resolve_public_addresses,
    validate_url,
)


SUPPORTED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/xml",
    "text/xml",
}
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.I)


@dataclass(frozen=True)
class RobotsDecision:
    allow_all: bool
    parser: RobotFileParser | None = None
    fetched_at: float = 0.0


class FetchService:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        policy: SecurityPolicy,
        user_agent: str = "DeepSeekBot/1.0",
        max_bytes: int = 5 * 1024 * 1024,
        max_redirects: int = 5,
        respect_robots: bool = True,
        robots_ttl_seconds: int = 3600,
        cache: TwoTierCache | None = None,
        coalescer: SingleFlight | None = None,
    ) -> None:
        self.client = client
        self.policy = policy
        self.user_agent = user_agent
        self.robot_product = user_agent.split("/", 1)[0]
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.respect_robots = respect_robots
        self.robots_ttl_seconds = robots_ttl_seconds
        self.cache = cache
        self.coalescer = coalescer or SingleFlight()
        self._robots_cache: dict[str, RobotsDecision] = {}

    async def fetch(self, raw_url: str) -> ExtractedPage:
        """Fetch a page, using the cache and coalescing identical requests."""

        key = _cache_key(raw_url)
        return await self.coalescer.run(key, lambda: self._fetch_uncached(raw_url, key))

    async def _fetch_uncached(self, raw_url: str, cache_key: str) -> ExtractedPage:
        try:
            target = validate_url(raw_url)
        except URLValidationError as exc:
            raise APIError(400, f"Invalid URL: {exc}", "invalid_url") from exc

        self.policy.check_host(target.hostname)
        if self.respect_robots:
            allowed = await self._robots_allows(target)
            if not allowed:
                raise APIError(
                    404,
                    "The page cannot be fetched because robots.txt disallows it",
                    "no_usable_content",
                )

        cached = await self._cache_get(cache_key)
        if cached is not None and cached.is_fresh():
            return _page_from_entry(cached)
        conditional = _conditional_headers(cached)

        try:
            final_url, response = await self._request_with_redirects(
                target,
                before_request=self._check_page_target,
                extra_headers=conditional,
            )
            if response.status_code == 304 and cached is not None:
                await self._cache_touch(cache_key)
                return _page_from_entry(cached)
            body = await self._read_limited(response, self.max_bytes)
            if response.status_code >= 500:
                raise APIError(500, "The downstream URL returned a server error", "upstream_fetch_failed")
            if response.status_code >= 400:
                raise APIError(
                    404,
                    "The downstream URL did not return a usable page",
                    "no_usable_content",
                )
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if not content_type:
                content_type = "text/html" if body.lstrip().startswith(b"<") else "text/plain"
            if content_type not in SUPPORTED_CONTENT_TYPES:
                raise APIError(
                    404,
                    "The URL returned an unsupported content type",
                    "unsupported_content_type",
                )

            charset_match = _CHARSET_RE.search(response.headers.get("content-type", ""))
            charset = charset_match.group(1) if charset_match else "utf-8"
            try:
                text = body.decode(charset, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")

            if content_type in {"text/html", "application/xhtml+xml"}:
                page = extract_page(text)
            elif content_type == "text/markdown":
                page = ExtractedPage(title="", content=sanitize_untrusted_text(text))
            elif content_type in {"application/xml", "text/xml"}:
                page = ExtractedPage(title="", content=sanitize_untrusted_text(text))
            else:
                page = ExtractedPage(title="", content=sanitize_untrusted_text(text))

            if not page.content.strip():
                raise APIError(
                    404,
                    "No usable content could be extracted from the URL",
                    "no_usable_content",
                )
            await self._cache_store(
                cache_key,
                page,
                content_type=content_type,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
            return page
        except httpx.ReadTimeout as exc:
            raise APIError(408, "Timed out while reading the response body", "body_timeout") from exc
        except httpx.TimeoutException as exc:
            raise APIError(504, "The downstream URL timed out", "downstream_timeout") from exc
        except httpx.RequestError as exc:
            raise APIError(404, "The URL could not be fetched", "no_usable_content") from exc

    async def _check_page_target(self, target: ValidatedURL) -> None:
        self.policy.check_host(target.hostname)
        if self.respect_robots and not await self._robots_allows(target):
            raise APIError(
                404,
                "The page cannot be fetched because robots.txt disallows it",
                "no_usable_content",
            )

    async def _robots_allows(self, target: ValidatedURL) -> bool:
        cache_key = f"{target.scheme}://{target.hostname}:{target.port}"
        cached = self._robots_cache.get(cache_key)
        now = time.monotonic()
        if cached and now - cached.fetched_at < self.robots_ttl_seconds:
            decision = cached
        else:
            decision = await self._load_robots(target)
            self._robots_cache[cache_key] = decision

        if decision.allow_all:
            return True
        if decision.parser is None:
            return False
        return decision.parser.can_fetch(self.robot_product, target.original)

    async def _load_robots(self, target: ValidatedURL) -> RobotsDecision:
        robots_url = urlunsplit((target.scheme, target.parsed.netloc, "/robots.txt", "", ""))
        try:
            robots_target = validate_url(robots_url)
            _, response = await self._request_with_redirects(robots_target)
            body = await self._read_limited(response, 512 * 1024)
        except (APIError, httpx.HTTPError):
            return RobotsDecision(allow_all=True)

        if response.status_code in {404, 410}:
            return RobotsDecision(allow_all=True)
        if response.status_code in {401, 403}:
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            return RobotsDecision(allow_all=False, parser=parser)
        if not 200 <= response.status_code < 300:
            return RobotsDecision(allow_all=True)

        parser = RobotFileParser()
        try:
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
        except Exception:
            return RobotsDecision(allow_all=True)
        return RobotsDecision(allow_all=False, parser=parser)

    async def _request_with_redirects(
        self,
        initial: ValidatedURL,
        *,
        before_request=None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[ValidatedURL, httpx.Response]:
        current = initial
        for redirect_number in range(self.max_redirects + 1):
            if before_request is not None:
                await before_request(current)
            response = await self._request_once(
                current,
                extra_headers=extra_headers if redirect_number == 0 else None,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return current, response

            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise APIError(404, "The URL returned an invalid redirect", "no_usable_content")
            if redirect_number >= self.max_redirects:
                raise APIError(404, "The URL redirected too many times", "no_usable_content")

            next_url = urljoin(current.original, location)
            try:
                current = validate_url(next_url)
            except URLValidationError as exc:
                raise APIError(404, "The URL redirected to an invalid URL", "no_usable_content") from exc
        raise APIError(404, "The URL redirected too many times", "no_usable_content")

    async def _request_once(
        self,
        target: ValidatedURL,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.policy.check_host(target.hostname)
        addresses = await resolve_public_addresses(target.hostname)
        selected = addresses[0]
        pinned_url = _pin_hostname(target, selected)
        headers = {
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
            "User-Agent": self.user_agent,
            "Host": _host_header(target),
        }
        if extra_headers:
            headers.update(extra_headers)
        request = self.client.build_request(
            "GET",
            pinned_url,
            headers=headers,
            extensions={"sni_hostname": target.hostname},
        )
        return await self.client.send(request, stream=True)

    async def _cache_get(self, cache_key: str) -> CacheEntry | None:
        if self.cache is None:
            return None
        return await self.cache.get_for_revalidation(cache_key)

    async def _cache_store(
        self,
        cache_key: str,
        page: ExtractedPage,
        *,
        content_type: str,
        etag: str | None,
        last_modified: str | None,
    ) -> None:
        if self.cache is None:
            return
        await self.cache.set(
            cache_key,
            page.content,
            content_type=content_type,
            etag=etag,
            last_modified=last_modified,
            metadata={"title": page.title},
        )

    async def _cache_touch(self, cache_key: str) -> None:
        if self.cache is None:
            return
        await self.cache.touch(cache_key)

    async def _read_limited(self, response: httpx.Response, limit: int) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                await response.aclose()
                raise APIError(413, "The response body is too large", "body_too_large")
        await response.aclose()
        return bytes(body)


def _cache_key(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    normalized = urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, parsed.query, "")
    )
    return f"fetch:{normalized}"


def _conditional_headers(cached: CacheEntry | None) -> dict[str, str]:
    if cached is None:
        return {}
    headers: dict[str, str] = {}
    if cached.etag:
        headers["If-None-Match"] = cached.etag
    if cached.last_modified:
        headers["If-Modified-Since"] = cached.last_modified
    return headers


def _page_from_entry(entry: CacheEntry) -> ExtractedPage:
    return ExtractedPage(
        title=str(entry.metadata.get("title") or ""),
        content=entry.value,
    )


def _pin_hostname(target: ValidatedURL, address: str) -> str:
    parsed = urlsplit(target.original)
    literal = f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
    netloc = literal
    if target.explicit_port is not None:
        netloc = f"{netloc}:{target.explicit_port}"
    return urlunsplit((target.scheme, netloc, parsed.path or "/", parsed.query, ""))


def _host_header(target: ValidatedURL) -> str:
    hostname = target.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    if target.explicit_port is not None:
        return f"{hostname}:{target.explicit_port}"
    return hostname
