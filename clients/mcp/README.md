# SearchRelay MCP server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the
SearchRelay HTTP API as two agent tools:

- `search` — run a web search and return ranked titles, URLs, and snippets;
- `fetch` — fetch one URL and return the page as Markdown.

The server speaks the MCP **stdio** transport (newline-delimited JSON-RPC 2.0 on
stdin/stdout) and needs only `httpx`.

## Install

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e clients/mcp
```

## Configure

The server reads two environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `SEARCHRELAY_BASE_URL` | `http://localhost:8000` | Base URL of the SearchRelay API |
| `SEARCHRELAY_API_KEY` | empty | Bearer key for the SearchRelay API |

### Claude Desktop

Add the server to `claude_desktop_config.json`
(`~/Library/Application Support/Claude/` on macOS,
`%APPDATA%\Claude\` on Windows):

```json
{
  "mcpServers": {
    "searchrelay": {
      "command": "/absolute/path/to/.venv/bin/searchrelay-mcp",
      "env": {
        "SEARCHRELAY_BASE_URL": "http://localhost:8000",
        "SEARCHRELAY_API_KEY": "sk-your-key"
      }
    }
  }
}
```

Restart Claude Desktop after editing the file.

### Claude Code

```bash
claude mcp add searchrelay --env SEARCHRELAY_BASE_URL=http://localhost:8000 \
  --env SEARCHRELAY_API_KEY=sk-your-key -- /absolute/path/to/.venv/bin/searchrelay-mcp
```

List the configured servers with `claude mcp list`.

### Cursor

Add the same command and environment to
`~/.cursor/mcp.json` (or the workspace `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "searchrelay": {
      "command": "/absolute/path/to/.venv/bin/searchrelay-mcp",
      "env": {
        "SEARCHRELAY_BASE_URL": "http://localhost:8000",
        "SEARCHRELAY_API_KEY": "sk-your-key"
      }
    }
  }
}
```

### Windsurf

Add the server to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "searchrelay": {
      "command": "/absolute/path/to/.venv/bin/searchrelay-mcp",
      "env": {
        "SEARCHRELAY_BASE_URL": "http://localhost:8000",
        "SEARCHRELAY_API_KEY": "sk-your-key"
      }
    }
  }
}
```

## Verify

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | .venv/bin/searchrelay-mcp
```

Every tool result is third-party web content and is untrusted data, not
instructions. The server labels results accordingly.
