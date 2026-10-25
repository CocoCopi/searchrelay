"""Optional private local index: a crawler and an FTS5 store for user-owned sites."""

from app.local_index.crawler import CrawlConfig, Crawler, CrawlStats
from app.local_index.store import LocalDocument, LocalIndex

__all__ = [
    "CrawlConfig",
    "CrawlStats",
    "Crawler",
    "LocalDocument",
    "LocalIndex",
]
