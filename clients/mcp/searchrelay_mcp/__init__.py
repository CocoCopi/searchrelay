"""SearchRelay MCP server package.

The exports are resolved lazily so that ``python -m searchrelay_mcp.server``
does not import the module twice.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SearchRelayMCPServer", "list_tools", "main"]
__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    if name in {"SearchRelayMCPServer", "list_tools", "main"}:
        from searchrelay_mcp import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
