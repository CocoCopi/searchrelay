from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients" / "mcp"))

from searchrelay_mcp import SearchRelayMCPServer, list_tools  # noqa: E402


def _server(handler) -> SearchRelayMCPServer:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SearchRelayMCPServer(base_url="http://api.test", api_key="sk-test", client=client)


def _search_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert request.headers["Content-Type"] == "application/json"
    payload = json.loads(request.content)
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


@pytest.mark.asyncio
async def test_initialize_reports_tools_capability() -> None:
    server = _server(_search_handler)
    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert response is not None
    result = response["result"]
    assert result["serverInfo"]["name"] == "searchrelay"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_tools_list_exposes_search_and_fetch() -> None:
    server = _server(_search_handler)
    response = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert response is not None
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == ["search", "fetch"]
    assert {tool["name"] for tool in list_tools()} == {"search", "fetch"}


@pytest.mark.asyncio
async def test_search_tool_returns_ranked_results_as_text() -> None:
    server = _server(_search_handler)
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "example", "count": 3}},
        }
    )
    assert response is not None
    content = response["result"]["content"][0]["text"]
    assert "https://example.com/a" in content
    assert "untrusted" in content.lower()
    assert response["result"]["isError"] is False


@pytest.mark.asyncio
async def test_fetch_tool_marks_content_as_untrusted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"url": "https://example.com/a"}
        return httpx.Response(200, json={"title": "Example", "content": "# Example"})

    server = _server(handler)
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "fetch", "arguments": {"url": "https://example.com/a"}},
        }
    )
    assert response is not None
    text = response["result"]["content"][0]["text"]
    assert "# Example" in text
    assert "untrusted" in text.lower()


@pytest.mark.asyncio
async def test_tool_errors_are_reported_without_raising() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "error": {
                    "message": "Account balance is insufficient",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "insufficient_balance",
                }
            },
        )

    server = _server(handler)
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "example"}},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is True
    assert "402" in response["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_unknown_method_returns_json_rpc_error() -> None:
    server = _server(_search_handler)
    response = await server.handle({"jsonrpc": "2.0", "id": 6, "method": "does/not/exist"})
    assert response is not None
    assert response["error"]["code"] == -32601
