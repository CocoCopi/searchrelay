"""Search provider backed by the optional private local index.

This provider searches **only** the pages the operator asked SearchRelay to
crawl (their own sites, documentation, or internal pages). It is not a web
index, and it never reaches out to the public web.
"""

from __future__ import annotations

from pathlib import Path

from app.local_index.store import LocalIndex
from app.operators import ParsedQuery
from app.providers.base import ProviderSearchResult, SearchCandidate


class LocalIndexProvider:
    """Query the private FTS5 index through the standard provider interface."""

    name = "local"

    def __init__(self, index_path: Path | str) -> None:
        self.index_path = Path(index_path)
        self.index = LocalIndex(self.index_path)

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult:
        query = _query_text(parsed)
        if not query:
            return ProviderSearchResult(provider=self.name, candidates=(), providers_used=(self.name,))

        documents = self.index.search(query, limit=max(count * 3, count))
        candidates = tuple(
            candidate
            for document in documents
            if (candidate := _candidate(document)) is not None
        )
        return ProviderSearchResult(
            provider=self.name,
            candidates=candidates,
            providers_used=(self.name,),
        )

    async def healthcheck(self) -> bool:
        try:
            self.index.initialize()
        except Exception:
            return False
        return self.index_path.exists()


def _query_text(parsed: ParsedQuery) -> str:
    parts: list[str] = []
    parts.extend(parsed.terms)
    parts.extend(parsed.phrases)
    return " ".join(parts).strip()


def _candidate(document) -> SearchCandidate:
    snippet = document.content.strip().replace("\n", " ")[:2000]
    return SearchCandidate(
        title=document.title or document.url,
        url=document.url,
        snippet=snippet,
        date=document.fetched_at,
        engine_score=None,
        source_engine="local",
        language=document.language,
        provider="local",
        providers=("local",),
    )
