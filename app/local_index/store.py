"""SQLite FTS5 store for the optional private local index.

This index holds **only the pages the operator asked us to crawl**: their own
sites, documentation, or internal pages. It is not a web index and SearchRelay
does not claim to own one.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


@dataclass(frozen=True)
class LocalDocument:
    url: str
    title: str
    content: str
    language: str | None
    fetched_at: datetime | None
    score: float


class LocalIndex:
    """A small FTS5 index over crawled pages."""

    def __init__(self, database_path: Path | str) -> None:
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
                    CREATE TABLE IF NOT EXISTS documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL UNIQUE,
                        host TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        language TEXT,
                        fetched_at TEXT,
                        content_hash TEXT NOT NULL,
                        content TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        title,
                        content,
                        content='documents',
                        content_rowid='id',
                        tokenize='porter unicode61'
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                        INSERT INTO documents_fts(rowid, title, content)
                        VALUES (new.id, new.title, new.content);
                    END
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                        INSERT INTO documents_fts(documents_fts, rowid, title, content)
                        VALUES ('delete', old.id, old.title, old.content);
                    END
                    """
                )
                connection.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                        INSERT INTO documents_fts(documents_fts, rowid, title, content)
                        VALUES ('delete', old.id, old.title, old.content);
                        INSERT INTO documents_fts(rowid, title, content)
                        VALUES (new.id, new.title, new.content);
                    END
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS documents_host ON documents(host)"
                )
                connection.commit()
            self._initialized = True

    def upsert(
        self,
        *,
        url: str,
        title: str,
        content: str,
        language: str | None = None,
        fetched_at: datetime | None = None,
    ) -> None:
        """Insert or replace one document."""

        self.initialize()
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").casefold()
        timestamp = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO documents (
                        url, host, title, language, fetched_at, content_hash, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        host = excluded.host,
                        title = excluded.title,
                        language = excluded.language,
                        fetched_at = excluded.fetched_at,
                        content_hash = excluded.content_hash,
                        content = excluded.content
                    """,
                    (
                        url,
                        host,
                        title,
                        language,
                        timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
                        content_hash,
                        content,
                    ),
                )
                connection.commit()

    def search(self, query: str, *, limit: int = 10) -> list[LocalDocument]:
        """Return documents matching ``query``, best match first."""

        match_query = build_match_query(query)
        if not match_query:
            return []
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT d.url, d.title, d.content, d.language, d.fetched_at,
                           bm25(documents_fts) AS score
                    FROM documents_fts
                    JOIN documents d ON d.id = documents_fts.rowid
                    WHERE documents_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (match_query, max(1, limit)),
                ).fetchall()

        documents: list[LocalDocument] = []
        for url, title, content, language, fetched_at, score in rows:
            documents.append(
                LocalDocument(
                    url=url,
                    title=title or url,
                    content=content or "",
                    language=language,
                    fetched_at=_parse_timestamp(fetched_at),
                    score=float(score),
                )
            )
        return documents

    def count(self) -> int:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                row = connection.execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0]) if row else 0

    def hosts(self) -> list[str]:
        self.initialize()
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT DISTINCT host FROM documents ORDER BY host"
                ).fetchall()
        return [str(row[0]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10)


def build_match_query(query: str) -> str:
    """Turn a free-text query into a safe FTS5 MATCH expression.

    Terms are quoted so that FTS operators in user input cannot change the
    query, and phrases are kept intact.
    """

    parts: list[str] = []
    for chunk in re.findall(r'"([^"]+)"|(\S+)', query):
        phrase, term = chunk
        value = (phrase or term).strip()
        if not value:
            continue
        tokens = _TOKEN_RE.findall(value)
        if not tokens:
            continue
        if phrase:
            parts.append(f'"{_escape(tokens[0])}"' if len(tokens) == 1 else '"' + " ".join(tokens) + '"')
        else:
            parts.extend(f'"{_escape(token)}"' for token in tokens)
    if not parts:
        return ""
    return " AND ".join(parts)


def _escape(token: str) -> str:
    return token.replace('"', '""')


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
