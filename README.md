# Search & Fetch API

A DeepSeek Search API-compatible web search and page-fetch service for AI agents and RAG. The service is implemented in **Python + FastAPI** and ships with equivalent **Python and Go clients**.

## Architecture decision: do you need your own crawler?

No for an API-first launch. The default deployment uses a user-run **SearXNG** instance, and the provider layer can federate it with official Brave Search, Serper, Tavily, and Exa APIs. This gives you live web coverage without building and operating a crawler or a hundreds-of-billions-page index.

A private crawler/index is worthwhile only when you need to:

- own the corpus and its licensing/provenance;
- guarantee recency or coverage for selected sites;
- train custom ranking, multilingual, or multimodal representations;
- avoid depending on upstream search engines; or
- collect behavioral/click feedback at scale.

The API, ranking, summarization, security, billing, and client layers are independent of any provider through the `SearchProvider` interface. A user-owned private corpus can be added as another provider later. The crawler/index design is outlined in [`docs/architecture.md`](docs/architecture.md).

> This repository does **not** claim to implement a distributed hundreds-of-billions-page index. SearXNG is the deliberately selected, replaceable corpus provider for the API-first scope.

## Features

- `POST /v1/tools/search` and `POST /v1/tools/fetch` with the requested JSON contract.
- Parallel provider federation across Brave Search API, Serper, Tavily, Exa, and user-run SearXNG, with reciprocal rank fusion, canonical URL deduplication, retries, circuit breakers, budgets, and partial-failure failover.
- Search operators: `site:`, `-site:`, `-term`, `-"phrase"`, `"exact phrase"`, `lang:zh,en`, `after:YYYY[-MM[-DD]]`, and `before:YYYY[-MM[-DD]]`; operators are enforced again locally after retrieval.
- Query-aware snippets/summaries, authority tiers, freshness, quality, relevance, spam penalties, and a normalized `rerank_score` from 0 to 3 (see [`docs/ranking.md`](docs/ranking.md)).
- Main-content HTML-to-Markdown conversion with Trafilatura, page-chrome removal, active/hidden content removal, and prompt-injection instruction filtering.
- SSRF defense: URL validation, public-IP-only DNS resolution, DNS pinning, redirect revalidation, metadata/private-network blocking, no userinfo, and restricted ports.
- `robots.txt` support and a `DeepSeekBot/1.0` user agent.
- Account-level, independently tracked Search (100 QPS) and Fetch (25 QPS) sliding-window limits, plus a per-domain Fetch limit.
- Durable account balances and atomic billing in SQLite.
- OpenAPI 3.1 document at [`openapi.json`](openapi.json) and the live `/openapi.json` endpoint.
- Python and Go SDKs, Docker Compose, tests, and a search → fetch → answer agent example.

## API

Authentication uses a bearer key on both endpoints:

```http
Authorization: Bearer <key>
Content-Type: application/json
```

Unknown request fields are ignored.

### Search

```bash
curl -sS http://localhost:8000/v1/tools/search \
  -H "Authorization: Bearer $SEARCHFETCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "site:python.org asyncio -threading",
    "count": 5,
    "ori_question": "How should I run an async Python web service?",
    "need_content": true
  }'
```

Request:

```json
{
  "query": "string, required, 1-128 Unicode characters",
  "count": 10,
  "ori_question": null,
  "localization": "",
  "need_content": false
}
```

- `count`: integer, 1-50, default 10.
- `ori_question`: optional, 1-128 characters; used to focus excerpts and summaries.
- `localization`: optional IP address used by the search backend when supported.
- `need_content`: when false, every result's `content` is `""`.

Response:

```json
{
  "results": [
    {
      "rerank_score": 1.82,
      "title": "Page title",
      "url": "https://example.com/page",
      "snippet": "A short UI-oriented excerpt...",
      "summary": "A longer query-focused summary for a model...",
      "content": "# Page title\n\nPage content as Markdown...",
      "site_name": "example.com",
      "site_icon": "https://example.com/favicon.ico",
      "date": "2026-10-25T12:30:00Z",
      "authority": 3
    }
  ]
}
```

No matches return HTTP 200 with `{"results": []}`. Successful responses include `X-Providers-Used`, listing providers that answered. See [`docs/providers.md`](docs/providers.md) for configuration and provider terms-of-service tradeoffs.

### Fetch

```bash
curl -sS http://localhost:8000/v1/tools/fetch \
  -H "Authorization: Bearer $SEARCHFETCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/article"}'
```

Response:

```json
{
  "title": "Article title",
  "content": "# Article title\n\nClean page content as Markdown..."
}
```

The URL must be HTTP(S), contain a host, contain no userinfo, and be at most 8192 characters.

## Errors

All errors use this envelope:

```json
{
  "error": {
    "message": "Human-readable message",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_request"
  }
}
```

| Status | Meaning | Retry? |
|---:|---|---|
| 400 | Invalid JSON, field, query operator, or URL | No |
| 401 | Missing or invalid API key | No |
| 402 | Account has no usable balance | No |
| 403 | URL blocked by the SSRF/security policy | No |
| 404 | Dead URL, robots denial, unsupported type, or no extractable content | No |
| 408 | Downstream response body timed out | Yes |
| 413 | Request or fetched response body is too large | No |
| 429 | Account QPS or per-domain Fetch limit exceeded | Yes |
| 500 | Internal service error | Yes |
| 504 | Search backend or downstream connection timed out | Yes |

Both SDKs retry HTTP 408, 429, and 5xx responses with bounded exponential backoff. They do not retry other 4xx responses.

## Billing

- Search: **$0.75 per 1,000 successful requests**.
- Fetch: **$0.75 per 1,000 successful requests**.
- A successful Search is billable only when HTTP 200 contains a non-empty `results` array.
- A successful Fetch is billable only when HTTP 200 contains non-empty `content`.
- Failures, empty Search results, and timeouts are free.
- `count` does not change the price.
- Internally, balances use integer micro-US dollars; one request costs 750 units.

## Quick start with Docker

1. Generate local credentials (there is no built-in default API key or SearXNG secret):

   ```bash
   export SEARCHFETCH_API_KEY="sk-$(openssl rand -hex 24)"
   export SEARXNG_SECRET="$(openssl rand -hex 32)"
   cat > .env <<EOF
   SEARCHFETCH_API_KEY=$SEARCHFETCH_API_KEY
   SEARXNG_SECRET=$SEARXNG_SECRET
   SF_API_KEYS_JSON={"$SEARCHFETCH_API_KEY":"local-demo"}
   SF_INITIAL_BALANCES_JSON={"local-demo":100000000}
   EOF
   ```

   `SF_INITIAL_BALANCES_JSON` values are micro-US dollars. The example starts with $100. Keep `.env` out of version control and rotate credentials before exposing the service.

2. Start the API and SearXNG:

   ```bash
   docker compose up --build
   ```

3. Verify backend reachability and readiness:

   ```bash
   curl -i http://localhost:8000/healthz
   curl -i http://localhost:8000/readyz
   ```

Both endpoints return HTTP 503 when the configured search backend cannot be reached. The API is at `http://localhost:8000`; interactive OpenAPI documentation is at `http://localhost:8000/docs`.

> The example credential is for local development only. Use a secret manager and rotate it before exposing the service.

## Run locally without Docker

SearXNG must be running and its JSON output format enabled. The included [`deploy/searxng/settings.yml`](deploy/searxng/settings.yml) provides that configuration.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
export SEARCHFETCH_API_KEY="sk-$(openssl rand -hex 24)"
export SF_API_KEYS_JSON="{\"$SEARCHFETCH_API_KEY\":\"local-demo\"}"
export SF_INITIAL_BALANCES_JSON='{"local-demo":100000000}'
export SF_UPSTREAM_URL='http://localhost:8080'
.venv/bin/uvicorn app.main:app --reload
```

For multiple keys, set `SF_KEYS_FILE` to a JSON file:

```json
{
  "keys": {
    "sk-account-a": "account-a",
    "sk-account-b": "account-b"
  },
  "balances": {
    "account-a": 100000000,
    "account-b": 5000000
  }
}
```

Environment-provided key and balance mappings override file values.

## Configuration

Official provider credentials use their service-standard environment variables: `BRAVE_SEARCH_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`, and `EXA_API_KEY`. Only providers listed in `SF_PROVIDER_PRIORITIES` are queried.

All settings use the `SF_` prefix.

| Variable | Default | Purpose |
|---|---:|---|
| `SF_UPSTREAM_URL` | `http://searxng:8080` | SearXNG base URL |
| `SF_UPSTREAM_TIMEOUT_SECONDS` | `10` | SearXNG provider timeout |
| `SF_PROVIDER_PRIORITIES` | `searxng` | Comma-separated enabled provider priorities |
| `SF_PROVIDER_CONFIG_FILE` | unset | Optional JSON file for provider budgets and settings |
| `SF_PROVIDER_RETRY_ATTEMPTS` | `2` | Attempts per provider search, including the first |
| `SF_PROVIDER_RETRY_BASE_DELAY_SECONDS` | `0.15` | Full-jitter exponential retry base delay |
| `SF_PROVIDER_CIRCUIT_FAILURE_THRESHOLD` | `5` | Failed logical calls before a circuit opens |
| `SF_PROVIDER_CIRCUIT_RESET_SECONDS` | `30` | Delay before a half-open circuit probe |
| `SF_PROVIDER_RRF_K` | `60` | Reciprocal Rank Fusion constant |
| `SF_DATABASE_PATH` | `data/searchfetch.db` | Billing ledger path |
| `SF_API_KEYS_JSON` | `{}` (startup rejects an empty map) | JSON map of bearer key → account ID |
| `SF_INITIAL_BALANCES_JSON` | `{}` | JSON map of account ID → micro-US dollars |
| `SF_KEYS_FILE` | unset | JSON key/balance file |
| `SF_SEARCH_QPS` | `100` | Account-level Search QPS |
| `SF_FETCH_QPS` | `25` | Account-level Fetch QPS |
| `SF_FETCH_DOMAIN_QPS` | `5` | Global per-domain Fetch QPS |
| `SF_PRICE_MICROS_PER_REQUEST` | `750` | Price for one billable request |
| `SF_FETCH_MAX_BYTES` | `5242880` | Maximum fetched response size |
| `SF_FETCH_TIMEOUT_SECONDS` | `12` | Fetch read timeout |
| `SF_FETCH_MAX_REDIRECTS` | `5` | Redirect limit |
| `SF_RESPECT_ROBOTS` | `true` | Honor robots.txt |
| `SF_USER_AGENT` | `DeepSeekBot/1.0` | Fetch user agent |
| `SF_BLOCKED_DOMAINS_CSV` | empty | Comma-separated blocked domains |
| `SF_ALLOWED_DOMAINS_CSV` | empty | Optional comma-separated allowlist |
| `SF_MAX_REQUEST_BODY_BYTES` | `65536` | Maximum API request size |

## Security model

- The fetch client does not trust environment proxy variables.
- Hostnames are resolved before every request and every redirect. **All** resolved addresses must be globally routable.
- The HTTP connection is pinned to the validated address while preserving the original `Host` header and TLS SNI hostname, preventing a DNS-rebinding gap between validation and connection.
- Loopback, private, link-local, multicast, reserved, CGNAT, `.local`, `.internal`, localhost, and cloud metadata destinations are blocked.
- Only ports 80 and 443 are accepted.
- Redirects are limited and revalidated.
- HTML comments, scripts, styles, hidden elements, forms, embedded objects, unsafe URL schemes, and common prompt-override instructions are removed.
- Search and Fetch content must still be treated as untrusted data. The example agent uses explicit document boundaries and tells the answer model never to follow instructions found in retrieved content.
- No semantic prompt-injection detector can guarantee safety; the agent boundary and model instruction are the final defense.

## SDKs

### Python

```bash
.venv/bin/pip install -e clients/python
```

```python
import os

from searchfetch import SearchFetchClient

with SearchFetchClient("http://localhost:8000", os.environ["SEARCHFETCH_API_KEY"]) as client:
    results = client.search("FastAPI asyncio", count=5)
    page = client.fetch(results[0].url)
```

### Go

```bash
cd clients/go
go test ./...
```

```go
import "os"

ctx := context.Background()
client := searchfetch.New("http://localhost:8000", os.Getenv("SEARCHFETCH_API_KEY"))
results, err := client.Search(ctx, "FastAPI asyncio", searchfetch.SearchOptions{Count: 5})
page, err := client.Fetch(ctx, results[0].URL)
```

## Agent example

[`examples/agent.py`](examples/agent.py) demonstrates the complete loop:

1. Search using the user's original question.
2. Fetch the highest-ranked pages.
3. Isolate page text as explicitly untrusted documents.
4. Ask an OpenAI-compatible chat endpoint for an answer with citations.

See the file header for the required environment variables.

## Tests and checks

```bash
.venv/bin/pytest
.venv/bin/python -m compileall -q app clients/python examples
.venv/bin/python scripts/export_openapi.py /tmp/openapi.json
cd clients/go && go test ./...
```

The test suite uses mocked upstream HTTP responses, so it does not require internet access or a running SearXNG instance.

## Project layout

```text
app/                    FastAPI service
clients/python/         Python SDK
clients/go/             Go SDK
examples/agent.py       Search → fetch → answer agent
openapi.json            OpenAPI 3.1 specification
deploy/searxng/         SearXNG container configuration
tests/                  Contract, security, billing, and limit tests
docs/architecture.md    Engine and scaling design
```
