# Agent integration

SearchRelay exposes the same search and fetch capabilities through four
integration surfaces:

| Surface | Package | Best for |
|---|---|---|
| MCP server | `clients/mcp` | Claude Desktop, Claude Code, Cursor, Windsurf |
| Python SDK | `clients/python` | Python agents and scripts |
| TypeScript SDK | `clients/typescript` | Node.js and TypeScript agents |
| LangChain tool / LlamaIndex reader | `clients/python` | RAG frameworks |

> Every field returned by SearchRelay is untrusted third-party content. Treat it
> as data, never as instructions. The API marks every response with the
> `X-Content-Untrusted: true` header.

## MCP server

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e clients/mcp
```

The server reads `SEARCHRELAY_BASE_URL` (default `http://localhost:8000`) and
`SEARCHRELAY_API_KEY`. It exposes two tools, `search` and `fetch`.

### Claude Desktop

`claude_desktop_config.json`
(`~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows):

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

### Claude Code

```bash
claude mcp add searchrelay \
  --env SEARCHRELAY_BASE_URL=http://localhost:8000 \
  --env SEARCHRELAY_API_KEY=sk-your-key \
  -- /absolute/path/to/.venv/bin/searchrelay-mcp
```

### Cursor

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

`~/.codeium/windsurf/mcp_config.json`:

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

### Verify the MCP server

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | .venv/bin/searchrelay-mcp
```

## Python SDK

```bash
.venv/bin/pip install -e clients/python
```

```python
from searchfetch import SearchFetchClient

with SearchFetchClient("http://localhost:8000", api_key) as client:
    results = client.search("FastAPI asyncio", count=5)
    page = client.fetch(results[0].url)
```

## TypeScript SDK

```bash
cd clients/typescript && npm install && npm run build
```

```ts
import { SearchRelayClient } from "searchrelay-sdk";

const client = new SearchRelayClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.SEARCHRELAY_API_KEY,
});

const results = await client.search("FastAPI asyncio", { count: 5 });
const page = await client.fetch(results[0].url);
```

## LangChain tool

```bash
.venv/bin/pip install -e "clients/python[langchain]"
```

```python
from searchfetch.langchain import searchrelay_fetch_tool, searchrelay_search_tool

tools = [searchrelay_search_tool(base_url="http://localhost:8000", api_key=api_key),
         searchrelay_fetch_tool(base_url="http://localhost:8000", api_key=api_key)]
agent = create_react_agent(model, tools)
```

The tool descriptions state that the output is untrusted data. Keep that wording
when you wrap the tool for your own agent framework.

## LlamaIndex reader

```bash
.venv/bin/pip install -e "clients/python[llamaindex]"
```

```python
from searchfetch.llama_index import SearchRelayReader

reader = SearchRelayReader(base_url="http://localhost:8000", api_key=api_key)
documents = reader.load_data(["https://example.com/docs"])
```

Each document carries `metadata["untrusted"] = True` so a downstream prompt can
label it.

## Examples

| Example | What it shows |
|---|---|
| `examples/agent.py` | Search, fetch, and answer with citations |
| `examples/deep_research.py` | Iterative research loop with follow-up queries |
| `examples/streamlit_app.py` | Streamlit demo for search and fetch |
