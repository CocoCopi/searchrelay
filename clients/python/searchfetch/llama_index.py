"""LlamaIndex reader integration for SearchRelay.

The integration is optional. Install the extra with::

    pip install "searchfetch-python[llamaindex]"

The reader turns one or more URLs into LlamaIndex ``Document`` objects. The
document text is untrusted third-party content.
"""

from __future__ import annotations

from typing import Any, Iterable, List

from .client import APIError, SearchFetchClient

_IMPORT_ERROR_MESSAGE = (
    "The LlamaIndex integration requires llama-index-core. "
    "Install it with: pip install 'searchfetch-python[llamaindex]'"
)

try:  # pragma: no cover - exercised through the import-error branch in tests
    from llama_index.core.readers.base import BaseReader
    from llama_index.core.schema import Document

    _IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - depends on the environment
    BaseReader = object  # type: ignore[assignment,misc]
    Document = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


if _IMPORT_ERROR is None:

    class SearchRelayReader(BaseReader):  # type: ignore[misc, valid-type]
        """Read web pages through the SearchRelay fetch API."""

        def __init__(
            self,
            client: SearchFetchClient | None = None,
            *,
            base_url: str = "http://localhost:8000",
            api_key: str = "",
            max_pages: int = 10,
            skip_errors: bool = True,
        ) -> None:
            self._client = client or SearchFetchClient(base_url=base_url, api_key=api_key)
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
                yield Document(
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
    client: SearchFetchClient | None = None,
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
