"""Two-tier response cache and request coalescing.

The first tier is an in-process LRU cache. The second tier is an optional
SQLite table that survives restarts and can be shared between replicas that
mount the same volume. Both tiers are keyed by an opaque string and store a
payload plus the validators (``ETag`` / ``Last-Modified``) needed for
conditional revalidation.

Request coalescing is implemented with a small single-flight helper so that
concurrent identical queries or fetches share one upstream call instead of
stampeding the provider.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


DEFAULT_TTLS: dict[str, float] = {
    "search": 300.0,
    "text/html": 3600.0,
    "application/xhtml+xml": 3600.0,
    "text/plain": 3600.0,
    "text/markdown": 3600.0,
    "application/pdf": 86400.0,
    "application/json": 600.0,
    "application/xml": 600.0,
    "text/xml": 600.0,
}


@dataclass(frozen=True)
class CacheEntry:
    key: str
    value: str
    content_type: str = "text/plain"
    etag: str | None = None
    last_modified: str | None = None
    created_at: float = 0.0
    expires_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_fresh(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) < self.expires_at


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0
    memory_hits: int = 0
    disk_hits: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "memory_hits": self.memory_hits,
            "disk_hits": self.disk_hits,
        }


class MemoryCache:
    """Small thread-safe LRU cache with per-entry expiry."""

    def __init__(self, max_entries: int = 512) -> None:
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, *, allow_expired: bool = False) -> CacheEntry | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if not entry.is_fresh(now):
                if not allow_expired:
                    self._entries.pop(key, None)
                    return None
                return entry
            self._entries.move_to_end(key)
            return entry

    def set(self, entry: CacheEntry) -> None:
        with self._lock:
            self._entries[entry.key] = entry
            self._entries.move_to_end(entry.key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class SQLiteCache:
    """Persistent cache tier backed by a SQLite table."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        content_type TEXT NOT NULL,
                        etag TEXT,
                        last_modified TEXT,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS cache_entries_expires_at ON cache_entries(expires_at)"
                )
                connection.commit()
            self._initialized = True

    def get(self, key: str, *, allow_expired: bool = False) -> CacheEntry | None:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT value, content_type, etag, last_modified, created_at, expires_at, metadata
                    FROM cache_entries WHERE key = ?
                    """,
                    (key,),
                ).fetchone()
        if row is None:
            return None
        entry = CacheEntry(
            key=key,
            value=row[0],
            content_type=row[1],
            etag=row[2],
            last_modified=row[3],
            created_at=row[4],
            expires_at=row[5],
        )
        if not entry.is_fresh() and not allow_expired:
            self.delete(key)
            return None
        try:
            metadata = json.loads(row[6]) if row[6] else {}
        except json.JSONDecodeError:
            metadata = {}
        return CacheEntry(
            key=entry.key,
            value=entry.value,
            content_type=entry.content_type,
            etag=entry.etag,
            last_modified=entry.last_modified,
            created_at=entry.created_at,
            expires_at=entry.expires_at,
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def set(self, entry: CacheEntry) -> None:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO cache_entries (
                        key, value, content_type, etag, last_modified, created_at, expires_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        content_type = excluded.content_type,
                        etag = excluded.etag,
                        last_modified = excluded.last_modified,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at,
                        metadata = excluded.metadata
                    """,
                    (
                        entry.key,
                        entry.value,
                        entry.content_type,
                        entry.etag,
                        entry.last_modified,
                        entry.created_at,
                        entry.expires_at,
                        json.dumps(entry.metadata, ensure_ascii=False),
                    ),
                )
                connection.commit()

    def touch(self, key: str, expires_at: float) -> None:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE cache_entries SET expires_at = ? WHERE key = ?",
                    (expires_at, key),
                )
                connection.commit()

    def delete(self, key: str) -> None:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                connection.commit()

    def purge_expired(self) -> int:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM cache_entries WHERE expires_at <= ?", (time.time(),)
                )
                connection.commit()
                return cursor.rowcount or 0

    def clear(self) -> None:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                connection.execute("DELETE FROM cache_entries")
                connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10)


class TwoTierCache:
    """In-memory LRU cache in front of an optional SQLite cache."""

    def __init__(
        self,
        *,
        database_path: Path | None = None,
        max_entries: int = 512,
        ttls: dict[str, float] | None = None,
    ) -> None:
        self.memory = MemoryCache(max_entries=max_entries)
        self.disk = SQLiteCache(database_path) if database_path is not None else None
        self.ttls = dict(DEFAULT_TTLS)
        if ttls:
            self.ttls.update(ttls)
        self.stats = CacheStats()

    def ttl_for(self, content_type: str) -> float:
        return float(self.ttls.get(content_type, self.ttls.get("text/plain", 300.0)))

    async def get(self, key: str) -> CacheEntry | None:
        entry = self.memory.get(key)
        if entry is not None:
            self.stats.hits += 1
            self.stats.memory_hits += 1
            return entry
        if self.disk is not None:
            entry = await asyncio.to_thread(self.disk.get, key)
            if entry is not None:
                self.stats.hits += 1
                self.stats.disk_hits += 1
                self.memory.set(entry)
                return entry
        self.stats.misses += 1
        return None

    async def get_for_revalidation(self, key: str) -> CacheEntry | None:
        """Return an entry even when it is stale, so validators can be reused."""

        entry = self.memory.get(key, allow_expired=True)
        if entry is not None:
            return entry
        if self.disk is not None:
            entry = await asyncio.to_thread(self.disk.get, key, allow_expired=True)
            if entry is not None:
                self.memory.set(entry)
                return entry
        return None

    async def set(
        self,
        key: str,
        value: str,
        *,
        content_type: str = "text/plain",
        etag: str | None = None,
        last_modified: str | None = None,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> CacheEntry:
        now = time.time()
        ttl = self.ttl_for(content_type) if ttl_seconds is None else float(ttl_seconds)
        entry = CacheEntry(
            key=key,
            value=value,
            content_type=content_type,
            etag=etag,
            last_modified=last_modified,
            created_at=now,
            expires_at=now + ttl,
            metadata=dict(metadata or {}),
        )
        self.memory.set(entry)
        if self.disk is not None:
            await asyncio.to_thread(self.disk.set, entry)
        self.stats.stores += 1
        return entry

    async def touch(self, key: str, ttl_seconds: float | None = None) -> None:
        """Extend the lifetime of an entry after a successful revalidation."""

        entry = self.memory.get(key)
        ttl = self.ttl_for(entry.content_type) if ttl_seconds is None and entry else 300.0
        if ttl_seconds is None and entry is None:
            ttl = 300.0
        expires_at = time.time() + ttl
        if entry is not None:
            self.memory.set(
                CacheEntry(
                    key=entry.key,
                    value=entry.value,
                    content_type=entry.content_type,
                    etag=entry.etag,
                    last_modified=entry.last_modified,
                    created_at=entry.created_at,
                    expires_at=expires_at,
                    metadata=entry.metadata,
                )
            )
        if self.disk is not None:
            await asyncio.to_thread(self.disk.touch, key, expires_at)

    async def delete(self, key: str) -> None:
        self.memory.delete(key)
        if self.disk is not None:
            await asyncio.to_thread(self.disk.delete, key)

    async def clear(self) -> None:
        self.memory.clear()
        if self.disk is not None:
            await asyncio.to_thread(self.disk.clear)


class SingleFlight:
    """Collapse concurrent identical operations into one upstream call."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()
        self.coalesced_calls = 0

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                self.coalesced_calls += 1
                leader = False
            else:
                future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
                future.add_done_callback(_consume_exception)
                self._inflight[key] = future
                leader = True
                existing = future

        if not leader:
            return await asyncio.shield(existing)

        try:
            result = await factory()
        except BaseException as exc:
            async with self._lock:
                self._inflight.pop(key, None)
            if not existing.done():
                existing.set_exception(exc)
            raise
        async with self._lock:
            self._inflight.pop(key, None)
        if not existing.done():
            existing.set_result(result)
        return result


def _consume_exception(future: asyncio.Future[Any]) -> None:
    if future.cancelled():
        return
    future.exception()


def cache_key(*parts: object) -> str:
    """Build a stable cache key from structured parts."""

    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
