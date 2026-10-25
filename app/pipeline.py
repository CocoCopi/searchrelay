from __future__ import annotations

import asyncio
import re
from datetime import timezone
from urllib.parse import urlsplit

from app.content import sanitize_untrusted_text
from app.engine import SearchBackend, SearchCandidate
from app.errors import APIError
from app.excerpt import make_snippet, make_summary
from app.fetch import FetchService
from app.models import SearchRequest, SearchResult
from app.operators import ParsedQuery, date_is_in_range, parse_query
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
                    site_icon=_site_icon(candidate.url),
                    date=_format_date(candidate.date),
                    authority=signals.authority,
                )
            )
        return results

    def _filter_candidates(
        self,
        candidates: list[SearchCandidate],
        parsed: ParsedQuery,
        count: int,
    ) -> list[SearchCandidate]:
        """Enforce operators locally instead of trusting provider behavior."""

        filtered: list[SearchCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not date_is_in_range(candidate.date, parsed.after, parsed.before):
                continue
            try:
                parsed_url = urlsplit(candidate.url)
                host = (parsed_url.hostname or "").casefold().rstrip(".")
            except ValueError:
                continue
            if not host:
                continue
            if not _matches_sites(host, parsed.positive_sites, parsed.negative_sites):
                continue
            corpus = f"{candidate.title}\n{candidate.snippet}"
            if not _matches_phrases(corpus, parsed.phrases, parsed.excluded_phrases):
                continue
            if not _matches_excluded_terms(corpus, parsed.excluded_terms):
                continue
            if not _matches_language(candidate.language, parsed.languages):
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


def _site_icon(url: str) -> str | None:
    """Return the origin's conventional favicon URL without fetching it."""

    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host or parsed.scheme not in {"http", "https"}:
        return None
    authority = host.casefold()
    if ":" in authority:
        authority = f"[{authority}]"
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme.casefold()}://{authority}/favicon.ico"


def _matches_sites(host: str, positive: tuple[str, ...], negative: tuple[str, ...]) -> bool:
    if any(_host_matches(host, site) for site in negative):
        return False
    if positive and not any(_host_matches(host, site) for site in positive):
        return False
    return True


def _host_matches(host: str, site: str) -> bool:
    normalized = site.casefold().rstrip(".")
    return host == normalized or host.endswith(f".{normalized}")


def _matches_phrases(
    corpus: str,
    required: tuple[str, ...],
    excluded: tuple[str, ...],
) -> bool:
    normalized = _normalized_text(corpus)
    if any(_normalized_text(phrase) not in normalized for phrase in required):
        return False
    return all(_normalized_text(phrase) not in normalized for phrase in excluded)


def _matches_excluded_terms(corpus: str, excluded: tuple[str, ...]) -> bool:
    return all(not _contains_term(corpus, term) for term in excluded)


def _contains_term(corpus: str, term: str) -> bool:
    term = term.strip()
    if not term:
        return False
    if all(part.isalnum() or part == "_" for part in term):
        pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
        return re.search(pattern, corpus, flags=re.IGNORECASE | re.UNICODE) is not None
    return term.casefold() in corpus.casefold()


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _matches_language(language: str | None, requested: tuple[str, ...]) -> bool:
    if not requested:
        return True
    if not language:
        return False
    candidate = language.casefold().replace("_", "-")
    for expected in requested:
        normalized = expected.casefold().replace("_", "-")
        if candidate == normalized or candidate.startswith(f"{normalized}-"):
            return True
        if "-" not in normalized and "-" in candidate and candidate.split("-", 1)[0] == normalized:
            return True
    return False


def _format_date(value) -> str | None:
    if value is None:
        return None
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")
