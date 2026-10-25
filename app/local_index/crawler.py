"""A small, polite crawler for the optional private local index.

The crawler only visits **user-supplied seed sites**. It is not a web
crawler and SearchRelay does not claim to operate a web index.

Behaviour:

* it identifies itself as ``SearchRelayBot/1.0`` (override with
  ``--user-agent``);
* it reads and obeys ``robots.txt`` for every host;
* it waits between requests to the same host (``--delay``);
* it re-uses the API's SSRF policy, so private and link-local destinations are
  refused and every redirect is revalidated;
* it stores the main content of each page in the local FTS5 index.

Run it with::

    python -m app.local_index.crawl --seeds seeds.txt --index data/local-index.db
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.content import extract_page
from app.local_index.store import LocalIndex
from app.security import (
    SecurityPolicy,
    URLValidationError,
    ValidatedURL,
    host_header,
    pin_hostname,
    resolve_public_addresses,
    validate_url,
)


DEFAULT_USER_AGENT = "SearchRelayBot/1.0"


@dataclass
class CrawlConfig:
    seeds: list[str]
    max_pages: int = 50
    max_depth: int = 2
    delay_seconds: float = 1.0
    user_agent: str = DEFAULT_USER_AGENT
    max_bytes: int = 2 * 1024 * 1024
    timeout_seconds: float = 15.0
    respect_robots: bool = True
    allowed_hosts: set[str] = field(default_factory=set)


@dataclass
class CrawlStats:
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    robots_denied: int = 0
    visited: list[str] = field(default_factory=list)


class Crawler:
    def __init__(
        self,
        *,
        config: CrawlConfig,
        index: LocalIndex,
        client: httpx.AsyncClient | None = None,
        policy: SecurityPolicy | None = None,
    ) -> None:
        self.config = config
        self.index = index
        self.client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds, follow_redirects=False, trust_env=False
        )
        self.policy = policy or SecurityPolicy()
        self._owns_client = client is None
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def run(self) -> CrawlStats:
        stats = CrawlStats()
        self.index.initialize()
        queue: list[tuple[str, int]] = [(seed, 0) for seed in self.config.seeds]
        seen: set[str] = set()

        while queue and len(stats.visited) < self.config.max_pages:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)

            try:
                document = await self._crawl_page(url)
            except _RobotsDenied:
                stats.robots_denied += 1
                continue
            except (URLValidationError, httpx.HTTPError, ValueError):
                stats.failed += 1
                continue

            stats.visited.append(url)
            if document is None:
                stats.skipped += 1
                continue

            title, content, links = document
            self.index.upsert(
                url=url,
                title=title,
                content=content,
                fetched_at=datetime.now(timezone.utc),
            )
            stats.indexed += 1

            if depth < self.config.max_depth:
                for link in links:
                    if link not in seen and self._is_allowed(link):
                        queue.append((link, depth + 1))

        if self._owns_client:
            await self.client.aclose()
        return stats

    def _is_allowed(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").casefold()
        if not host:
            return False
        if self.config.allowed_hosts:
            return host in self.config.allowed_hosts
        return any(host == seed or host.endswith(f".{seed}") for seed in self._seed_hosts())

    def _seed_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for seed in self.config.seeds:
            host = (urlsplit(seed).hostname or "").casefold()
            if host:
                hosts.add(host)
        return hosts

    async def _crawl_page(self, url: str) -> tuple[str, str, list[str]] | None:
        target = validate_url(url)
        self.policy.check_host(target.hostname)
        if self.config.respect_robots and not await self._robots_allows(target):
            raise _RobotsDenied(url)

        await self._wait_for_host(target.hostname)
        final_url, response = await self._request_with_redirects(target)
        body = response.content
        if len(body) > self.config.max_bytes:
            body = body[: self.config.max_bytes]
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
            return None

        text = body.decode(response.encoding or "utf-8", errors="replace")
        page = extract_page(text)
        if not page.content.strip():
            return None
        links = self._extract_links(text, final_url)
        return page.title, page.content, links

    def _extract_links(self, html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            candidate = urljoin(base_url, anchor["href"].strip())
            parsed = urlsplit(candidate)
            if parsed.scheme not in {"http", "https"}:
                continue
            if not parsed.hostname:
                continue
            links.append(candidate.split("#", 1)[0])
        return links

    async def _request_with_redirects(
        self,
        target: ValidatedURL,
        *,
        max_redirects: int = 3,
    ) -> tuple[str, httpx.Response]:
        current = target
        for _ in range(max_redirects + 1):
            addresses = await resolve_public_addresses(current.hostname)
            pinned_url = pin_hostname(current, addresses[0])
            request = self.client.build_request(
                "GET",
                pinned_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                    "User-Agent": self.config.user_agent,
                    "Host": host_header(current),
                },
                extensions={"sni_hostname": current.hostname},
            )
            response = await self.client.send(request, stream=True)
            if response.status_code not in {301, 302, 303, 307, 308}:
                body = await response.aread()
                response._content = body  # type: ignore[attr-defined]
                return current.original, response

            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise ValueError("redirect without a location header")
            current = validate_url(urljoin(current.original, location))
        raise ValueError("too many redirects")

    async def _wait_for_host(self, host: str) -> None:
        delay = self.config.delay_seconds
        if delay <= 0:
            return
        last = self._last_request_at.get(host)
        now = time.monotonic()
        if last is not None:
            remaining = delay - (now - last)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_request_at[host] = time.monotonic()

    async def _robots_allows(self, target: ValidatedURL) -> bool:
        key = f"{target.scheme}://{target.hostname}:{target.port}"
        if key not in self._robots:
            self._robots[key] = await self._load_robots(target)
        parser = self._robots[key]
        if parser is None:
            return True
        return parser.can_fetch(self.config.user_agent.split("/", 1)[0], target.original)

    async def _load_robots(self, target: ValidatedURL) -> RobotFileParser | None:
        robots_url = f"{target.scheme}://{target.hostname}/robots.txt"
        try:
            robots_target = validate_url(robots_url)
            addresses = await resolve_public_addresses(robots_target.hostname)
            pinned_url = pin_hostname(robots_target, addresses[0])
            request = self.client.build_request(
                "GET",
                pinned_url,
                headers={
                    "User-Agent": self.config.user_agent,
                    "Host": host_header(robots_target),
                },
                extensions={"sni_hostname": robots_target.hostname},
            )
            response = await self.client.send(request)
        except (URLValidationError, httpx.HTTPError):
            return None
        if response.status_code in {401, 403}:
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser
        if response.status_code >= 400:
            return None
        parser = RobotFileParser()
        try:
            parser.parse(response.text.splitlines())
        except Exception:
            return None
        return parser


class _RobotsDenied(Exception):
    pass


def _read_seeds(path: str | None, values: list[str]) -> list[str]:
    seeds = list(values)
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                candidate = line.strip()
                if candidate and not candidate.startswith("#"):
                    seeds.append(candidate)
    normalized: list[str] = []
    for seed in seeds:
        if "://" not in seed:
            seed = f"https://{seed}"
        normalized.append(seed)
    return normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.local_index.crawl",
        description="Crawl user-supplied seed sites into the local FTS5 index.",
    )
    parser.add_argument("--seeds", help="File with one seed URL or domain per line")
    parser.add_argument("--seed", action="append", default=[], help="Seed URL or domain")
    parser.add_argument("--index", default="data/local-index.db", help="Index database path")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--delay", type=float, default=1.0, help="Per-host politeness delay")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--ignore-robots", action="store_true")
    args = parser.parse_args(argv)

    seeds = _read_seeds(args.seeds, args.seed)
    if not seeds:
        parser.error("at least one seed is required (--seed or --seeds)")

    config = CrawlConfig(
        seeds=seeds,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay_seconds=args.delay,
        user_agent=args.user_agent,
        respect_robots=not args.ignore_robots,
    )
    index = LocalIndex(args.index)
    crawler = Crawler(config=config, index=index)
    stats = asyncio.run(crawler.run())
    print(
        f"indexed={stats.indexed} skipped={stats.skipped} failed={stats.failed} "
        f"robots_denied={stats.robots_denied} documents={index.count()}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
