"""LlamaIndex reader integration for SearchRelay.

The integration is optional. Install the extra with::

    pip install "searchrelay-python[llamaindex]"

The reader turns one or more URLs into LlamaIndex ``Document`` objects. The
document text is untrusted third-party content.
"""

from __future__ import annotations

import importlib
from typing import Any, Iterable, List

from .client import APIError, SearchRelayClient

_IMPORT_ERROR_MESSAGE = (
    "The LlamaIndex integration requires llama-index-core. "
    "Install it with: pip install 'searchrelay-python[llamaindex]'"
)

class _MissingBaseReader:
    """Fallback base used only when the optional dependency is absent."""


_BaseReader: Any
_Document: Any
try:  # pragma: no cover - exercised through the import-error branch in tests
    _base_module = importlib.import_module("llama_index.core.readers.base")
    _schema_module = importlib.import_module("llama_index.core.schema")
    _BaseReader = _base_module.BaseReader
    _Document = _schema_module.Document
    _IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - depends on the environment
    _BaseReader = _MissingBaseReader
    _Document = None
    _IMPORT_ERROR = exc


if _IMPORT_ERROR is None:

    class SearchRelayReader(_BaseReader):  # type: ignore[misc, valid-type]
        """Read web pages through the SearchRelay fetch API."""

        def __init__(
            self,
            client: SearchRelayClient | None = None,
            *,
            base_url: str = "http://localhost:8000",
            api_key: str = "",
            max_pages: int = 10,
            skip_errors: bool = True,
        ) -> None:
            self._client = client or SearchRelayClient(base_url=base_url, api_key=api_key)
            self._max_pages = max(1, int(max_pages))
            self._skip_errors = skip_errors

        def lazy_load_data(
            self,
            urls: Iterable[str],
            **_: Any,
        ) -> Iterable[Any]:
            for url in list(urls)[: self._max_pages]:
                try:
                    page = self._client.fetch(url)
                except APIError:
                    if self._skip_errors:
                        continue
                    raise
                yield _Document(
                    text=page.content,
                    metadata={
                        "title": page.title,
                        "url": url,
                        "source": "searchrelay",
                        "untrusted": True,
                    },
                )

else:  # pragma: no cover - depends on the environment

    class SearchRelayReader:  # type: ignore[no-redef]
        """Placeholder raised when llama-index-core is not installed."""

        def __init__(self, *_: Any, **__: Any) -> None:
            raise ImportError(_IMPORT_ERROR_MESSAGE) from _IMPORT_ERROR


def load_documents(
    urls: List[str],
    *,
    client: SearchRelayClient | None = None,
    base_url: str = "http://localhost:8000",
    api_key: str = "",
    max_pages: int = 10,
) -> List[Any]:
    """Load LlamaIndex documents for the given URLs."""

    reader = SearchRelayReader(
        client,
        base_url=base_url,
        api_key=api_key,
        max_pages=max_pages,
    )
    return list(reader.load_data(urls))
