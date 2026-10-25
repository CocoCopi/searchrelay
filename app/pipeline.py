from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import timezone
from urllib.parse import urlsplit

from app.cache import SingleFlight, TwoTierCache, cache_key
from app.content import sanitize_untrusted_text
from app.errors import APIError
from app.excerpt import make_snippet, make_summary
from app.fetch import FetchService
from app.models import SearchRequest, SearchResult
from app.operators import ParsedQuery, date_is_in_range, parse_query
from app.providers.base import SearchCandidate, SearchProvider
from app.ranking import Ranker, RankSignals
from app.rerank import CrossEncoderReranker

logger = logging.getLogger("searchrelay.pipeline")


@dataclass(frozen=True)
class SearchOutcome:
    results: tuple[SearchResult, ...]
    providers_used: tuple[str, ...]


class SearchPipeline:
    def __init__(
        self,
        *,
        backend: SearchProvider,
        fetcher: FetchService,
        ranker: Ranker | None = None,
        content_concurrency: int = 5,
        cache: TwoTierCache | None = None,
        coalescer: SingleFlight | None = None,
        cache_namespace: str = "search",
        cross_encoder: CrossEncoderReranker | None = None,
    ) -> None:
        self.backend = backend
        self.fetcher = fetcher
        self.ranker = ranker or Ranker()
        self.semaphore = asyncio.Semaphore(content_concurrency)
        self.cache = cache
        self.coalescer = coalescer or SingleFlight()
        self.cache_namespace = cache_namespace
        self.cross_encoder = cross_encoder

    async def search(self, request: SearchRequest) -> SearchOutcome:
        key = self._cache_key(request)
        return await self.coalescer.run(key, lambda: self._search_uncached(request, key))

    def _cache_key(self, request: SearchRequest) -> str:
        return cache_key(
            self.cache_namespace,
            request.query,
            request.count,
            request.ori_question or "",
            request.localization,
            request.need_content,
        )

    async def _search_uncached(self, request: SearchRequest, key: str) -> SearchOutcome:
        cached = await self._cache_get(key)
        if cached is not None:
            return _outcome_from_cache(cached.value)

        try:
            parsed = parse_query(request.query)
        except ValueError as exc:
            raise APIError(400, f"Invalid query operator: {exc}", "invalid_query") from exc

        provider_result = await self.backend.search(
            parsed,
            count=request.count,
            localization=request.localization,
        )
        candidates = self._filter_candidates(
            list(provider_result.candidates),
            parsed,
            request.count,
        )
        candidates.sort(key=lambda candidate: -candidate.fusion_score)

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
        prepared: list[tuple[SearchCandidate, str, str, str, str, RankSignals]] = []
        for candidate, page_content in materialized:
            source = page_content or candidate.snippet
            title = candidate.title or _site_name(candidate.url) or ""
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
            prepared.append((candidate, page_content, title, snippet, summary, signals))

        query_text = request.ori_question or request.query
        cross_scores = await self._cross_encoder_scores(
            query_text,
            [f"{title}\n{snippet}" for _, _, title, snippet, _, _ in prepared],
        )

        results: list[SearchResult] = []
        for (candidate, page_content, title, snippet, summary, signals), cross_score in zip(
            prepared, cross_scores
        ):
            results.append(
                SearchResult(
                    rerank_score=self.ranker.rerank_score(signals, cross_score),
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
        outcome = SearchOutcome(
            results=tuple(results),
            providers_used=provider_result.providers_used,
        )
        await self._cache_store(key, outcome)
        return outcome

    async def _cross_encoder_scores(
        self,
        query: str,
        documents: list[str],
    ) -> list[float | None]:
        """Score documents with the optional cross-encoder reranker.

        The reranker is off by default. When it is enabled but scoring fails,
        the heuristic score is used for every result instead of failing the
        request.
        """

        if self.cross_encoder is None or not documents:
            return [None] * len(documents)
        try:
            scores = await asyncio.to_thread(self.cross_encoder.score, query, documents)
        except Exception:
            logger.warning(
                "Cross-encoder reranking failed; using the heuristic score",
                exc_info=True,
            )
            return [None] * len(documents)
        return [float(score) for score in scores]

    async def _cache_get(self, key: str):
        if self.cache is None:
            return None
        return await self.cache.get(key)

    async def _cache_store(self, key: str, outcome: SearchOutcome) -> None:
        if self.cache is None:
            return
        payload = json.dumps(
            {
                "results": [result.model_dump() for result in outcome.results],
                "providers_used": list(outcome.providers_used),
            },
            ensure_ascii=False,
        )
        await self.cache.set(key, payload, content_type="search")

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


def _outcome_from_cache(payload: str) -> SearchOutcome:
    data = json.loads(payload)
    results = tuple(SearchResult.model_validate(item) for item in data.get("results", []))
    providers_used = tuple(str(name) for name in data.get("providers_used", []))
    return SearchOutcome(results=results, providers_used=providers_used)


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
