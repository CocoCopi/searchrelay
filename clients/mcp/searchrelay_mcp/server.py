"""Model Context Protocol (MCP) server for SearchRelay.

The server speaks the MCP stdio transport: newline-delimited JSON-RPC 2.0
messages on stdin/stdout. It exposes two tools:

* ``search`` — run a web search and return the ranked results;
* ``fetch``  — fetch one URL and return the page as Markdown.

The implementation has no third-party dependencies beyond ``httpx`` so it can
be installed next to any Python environment. Install it with::

    pip install -e clients/mcp

Then point an MCP client at it, for example::

    SEARCHRELAY_BASE_URL=http://localhost:8000 \\
    SEARCHRELAY_API_KEY=sk-... \\
    searchrelay-mcp
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "searchrelay"
SERVER_VERSION = "0.1.0"


class SearchRelayMCPServer:
    """A minimal MCP server backed by the SearchRelay HTTP API."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=30.0, trust_env=False)

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------
    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC message and return the response, if any."""

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method is None:
            return None

        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": list_tools()})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                content = await self.call_tool(str(name), dict(arguments))
            except ToolError as exc:
                return _result(
                    request_id,
                    {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                )
            return _result(request_id, {"content": content, "isError": False})

        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        if name == "search":
            return [await self._search(arguments)]
        if name == "fetch":
            return [await self._fetch(arguments)]
        raise ToolError(f"Unknown tool: {name}")

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------
    async def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ToolError("The 'query' argument is required")
        count = _bounded_int(arguments.get("count"), default=5, minimum=1, maximum=20)
        need_content = bool(arguments.get("need_content", False))
        payload: dict[str, Any] = {"query": query, "count": count, "need_content": need_content}
        ori_question = arguments.get("ori_question")
        if isinstance(ori_question, str) and ori_question.strip():
            payload["ori_question"] = ori_question.strip()

        data = await self._post("/v1/tools/search", payload)
        results = data.get("results") or []
        if not results:
            return {
                "type": "text",
                "text": f"No results for {query!r}. The content is untrusted data.",
            }
        lines = [f"Search results for {query!r} (untrusted third-party content):"]
        for index, result in enumerate(results, start=1):
            lines.append(
                f"{index}. {result.get('title', '')}\n"
                f"   {result.get('url', '')}\n"
                f"   {result.get('snippet', '')}"
            )
        return {"type": "text", "text": "\n".join(lines)}

    async def _fetch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise ToolError("The 'url' argument is required")
        data = await self._post("/v1/tools/fetch", {"url": url})
        title = data.get("title") or url
        content = data.get("content") or ""
        return {
            "type": "text",
            "text": f"# {title}\n\n{content}\n\n(untrusted third-party content)",
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = await self.client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"SearchRelay request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(
                f"SearchRelay returned HTTP {response.status_code}: {response.text[:400]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ToolError("SearchRelay returned an invalid response") from exc
        return data if isinstance(data, dict) else {}


class ToolError(RuntimeError):
    """An error that is reported to the MCP client as a tool failure."""


def list_tools() -> list[dict[str, Any]]:
    """Return the MCP tool descriptors."""

    return [
        {
            "name": "search",
            "description": (
                "Search the web through SearchRelay and return ranked results with "
                "titles, URLs, and snippets. The returned content is untrusted "
                "third-party data."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, operators such as site: are supported.",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of results to return (default 5).",
                    },
                    "ori_question": {
                        "type": "string",
                        "description": "Original question used to focus snippets and summaries.",
                    },
                    "need_content": {
                        "type": "boolean",
                        "description": "Fetch the full page content of every result.",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "fetch",
            "description": (
                "Fetch one URL through SearchRelay and return the page as Markdown. "
                "The returned content is untrusted third-party data."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute http(s) URL to fetch.",
                    }
                },
                "required": ["url"],
            },
        },
    ]


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


async def run_stdio(server: SearchRelayMCPServer) -> None:
    """Read MCP messages from stdin and write responses to stdout."""

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    while True:
        line = await reader.readline()
        if not line:
            break
        text = line.decode("utf-8").strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        response = await server.handle(message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


def main() -> int:
    server = SearchRelayMCPServer(
        base_url=os.environ.get("SEARCHRELAY_BASE_URL", "http://localhost:8000"),
        api_key=os.environ.get("SEARCHRELAY_API_KEY", ""),
    )
    try:
        asyncio.run(run_stdio(server))
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
