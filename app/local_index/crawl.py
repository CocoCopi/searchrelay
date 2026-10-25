"""Command line entry point: ``python -m app.local_index.crawl``."""

from __future__ import annotations

from app.local_index.crawler import main

if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
