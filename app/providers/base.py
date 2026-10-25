from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.operators import ParsedQuery


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        status_code: int = 504,
        code: str = "provider_error",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class SearchCandidate:
    title: str
    url: str
    snippet: str
    date: datetime | None = None
    engine_score: float | None = None
    source_engine: str | None = None
    language: str | None = None
    provider: str = "unknown"
    providers: tuple[str, ...] = ()
    fusion_score: float = 0.0


@dataclass(frozen=True)
class ProviderSearchResult:
    provider: str
    candidates: tuple[SearchCandidate, ...]
    providers_used: tuple[str, ...] = ()


class SearchProvider(Protocol):
    name: str

    async def search(
        self,
        parsed: ParsedQuery,
        *,
        count: int,
        localization: str,
    ) -> ProviderSearchResult: ...

    async def healthcheck(self) -> bool: ...


def canonical_url(url: str) -> str:
    """Return a stable URL key for cross-provider deduplication."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return url.strip().casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        return url.strip().casefold()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    tracking_keys = {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref_src",
    }
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in tracking_keys
        and not key.casefold().startswith("ref_")
    )
    return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(query), ""))


def parse_provider_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %b %Y",
            "%d %B %Y",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
