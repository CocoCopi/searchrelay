from __future__ import annotations

import asyncio
from datetime import timezone
from urllib.parse import urlsplit

from app.content import sanitize_untrusted_text
from app.engine import SearchBackend, SearchCandidate
from app.errors import APIError
from app.excerpt import make_snippet, make_summary
from app.fetch import FetchService
from app.models import SearchRequest, SearchResult
from app.operators import date_is_in_range, parse_query
from app.ranking import Ranker


class SearchPipeline:
    def __init__(
        self,
        *,
        backend: SearchBackend,
        fetcher: FetchService,
        ranker: Ranker | None = None,
        content_concurrency: int = 5,
    ) -> None:
        self.backend = backend
        self.fetcher = fetcher
        self.ranker = ranker or Ranker()
        self.semaphore = asyncio.Semaphore(content_concurrency)

    async def search(self, request: SearchRequest) -> list[SearchResult]:
        try:
            parsed = parse_query(request.query)
        except ValueError as exc:
            raise APIError(400, f"Invalid query operator: {exc}", "invalid_query") from exc

        candidates = await self.backend.search(
            parsed,
            count=request.count,
            localization=request.localization,
        )
        candidates = self._filter_candidates(candidates, parsed, request.count)

        async def materialize(candidate: SearchCandidate) -> tuple[SearchCandidate, str]:
            source = candidate.snippet
            page_content = ""
            needs_page = request.need_content or len(source) < 500
            if needs_page:
                async with self.semaphore:
                    try:
                        page = await self.fetcher.fetch(candidate.url)
                        page_content = page.content
                        source = page_content or source
                    except APIError:
                        page_content = ""
            return candidate, page_content

        materialized = await asyncio.gather(*(materialize(item) for item in candidates))
        results: list[SearchResult] = []
        for candidate, page_content in materialized:
            source = page_content or candidate.snippet
            title = candidate.title or _site_name(candidate.url)
            snippet = make_snippet(source, request.ori_question or request.query)
            summary = make_summary(source, request.ori_question or request.query)
            signals = self.ranker.signals(
                url=candidate.url,
                title=title,
                snippet=snippet,
                content=page_content or candidate.snippet,
                published_at=candidate.date,
                query_terms=parsed.search_terms,
                engine_score=candidate.engine_score,
            )
            results.append(
                SearchResult(
                    rerank_score=self.ranker.rerank_score(signals),
                    title=sanitize_untrusted_text(title),
                    url=candidate.url,
                    snippet=snippet,
                    summary=summary,
                    content=page_content if request.need_content else "",
                    site_name=_site_name(candidate.url),
                    site_icon=None,
                    date=_format_date(candidate.date),
                    authority=signals.authority,
                )
            )
        return results

    def _filter_candidates(
        self,
        candidates: list[SearchCandidate],
        parsed,
        count: int,
    ) -> list[SearchCandidate]:
        filtered: list[SearchCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not date_is_in_range(candidate.date, parsed.after, parsed.before):
                continue
            try:
                parsed_url = urlsplit(candidate.url)
                host = (parsed_url.hostname or "").casefold()
            except ValueError:
                continue
            if not host:
                continue
            normalized = f"{parsed_url.scheme.casefold()}://{parsed_url.netloc.casefold()}{parsed_url.path}"
            if normalized in seen:
                continue
            seen.add(normalized)
            filtered.append(candidate)
            if len(filtered) >= count:
                break
        return filtered


def _site_name(url: str) -> str | None:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.casefold().removeprefix("www.")


def _format_date(value) -> str | None:
    if value is None:
        return None
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")
