from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "python"))

from searchfetch import SearchFetchClient  # noqa: E402
from searchfetch.langchain import (  # noqa: E402
    searchrelay_fetch_tool,
    searchrelay_search_tool,
)
from searchfetch.llama_index import SearchRelayReader, load_documents  # noqa: E402

langchain_core = pytest.importorskip("langchain_core", reason="langchain-core is optional")
llama_index_core = pytest.importorskip("llama_index.core", reason="llama-index-core is optional")


def _client(handler) -> SearchFetchClient:
    return SearchFetchClient(
        "http://api.test",
        "sk-test",
        transport=httpx.MockTransport(handler),
    )


def _search_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/tools/search"
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "rerank_score": 1.5,
                    "title": "Example page",
                    "url": "https://example.com/a",
                    "snippet": "A snippet about the topic.",
                    "summary": "A summary.",
                    "content": "",
                    "site_name": "example.com",
                    "site_icon": None,
                    "date": None,
                    "authority": 2,
                }
            ]
        },
    )


def _fetch_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v1/tools/fetch"
    return httpx.Response(200, json={"title": "Example", "content": "# Example body"})


def test_langchain_search_tool_returns_untrusted_results() -> None:
    tool = searchrelay_search_tool(_client(_search_handler))
    assert tool.name == "searchrelay_search"
    assert "untrusted" in tool.description.lower()

    output = tool.invoke({"query": "example", "count": 2})
    assert "https://example.com/a" in output
    assert "untrusted" in output.lower()


def test_langchain_fetch_tool_returns_markdown() -> None:
    tool = searchrelay_fetch_tool(_client(_fetch_handler))
    assert tool.name == "searchrelay_fetch"
    output = tool.invoke({"url": "https://example.com/a"})
    assert "# Example body" in output
    assert "untrusted" in output.lower()


def test_llama_index_reader_loads_documents() -> None:
    reader = SearchRelayReader(_client(_fetch_handler), max_pages=2)
    documents = list(reader.load_data(["https://example.com/a"]))
    assert len(documents) == 1
    document = documents[0]
    assert document.text == "# Example body"
    assert document.metadata["url"] == "https://example.com/a"
    assert document.metadata["untrusted"] is True


def test_llama_index_reader_skips_failed_urls_by_default() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "Not found",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "not_found",
                }
            },
        )

    reader = SearchRelayReader(_client(handler))
    assert list(reader.load_data(["https://example.com/missing"])) == []


def test_load_documents_helper_respects_max_pages() -> None:
    documents = load_documents(
        ["https://example.com/a", "https://example.com/b"],
        client=_client(_fetch_handler),
        max_pages=1,
    )
    assert len(documents) == 1
