# API reference

SearchRelay implements the DeepSeek Search API shape. The contract is stable:
same field names, same error envelope, same billing semantics.

- Base URL: `http://localhost:8000` in the quickstart, your own host in production.
- Content type: `application/json` on every request.
- Authentication: `Authorization: Bearer <key>` on every `/v1/tools/*` call.

The machine-readable version of this page is the OpenAPI 3.1 document served
at `/openapi.json` and committed to the repository as `openapi.json`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/tools/search` | Search the web through the configured providers |
| `POST` | `/v1/tools/search/batch` | Run up to ten searches in one request |
| `POST` | `/v1/tools/fetch` | Fetch one URL and return clean Markdown |
| `GET` | `/healthz` | Liveness plus search-backend reachability |
| `GET` | `/readyz` | Readiness for traffic |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/openapi.json` | OpenAPI 3.1 document |
| `GET` | `/docs` | Interactive Swagger UI |

## Authentication

```http
Authorization: Bearer sk-your-key
```

Keys map to accounts. An account has a balance, independent Search and Fetch
rate limits, and a per-domain Fetch limit that applies across accounts.

```bash
export SEARCHRELAY_API_KEY="sk-$(openssl rand -hex 24)"
export SR_API_KEYS_JSON="{\"$SEARCHRELAY_API_KEY\":\"local-demo\"}"
export SR_INITIAL_BALANCES_JSON='{"local-demo":100000000}'
```

`SR_INITIAL_BALANCES_JSON` values are integer micro-US dollars: `100000000` is
$100.00.

A missing key returns `401` with `code: invalid_api_key`. A key whose account
has no usable balance returns `402`.

## `POST /v1/tools/search`

### Request

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `query` | string | yes | — | 1–128 characters, must not be blank |
| `count` | integer | no | `10` | 1–50, strict integer |
| `ori_question` | string or null | no | `null` | 1–128 characters; focuses snippets and summaries |
| `localization` | string | no | `""` | IPv4 or IPv6 address; validated as an IP address |
| `need_content` | boolean | no | `false` | When false, every result's `content` is `""` |

Unknown fields are ignored, so a client that sends extra keys still works.

```bash
curl -sS http://localhost:8000/v1/tools/search \
  -H "Authorization: Bearer $SEARCHRELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "site:python.org asyncio -threading",
    "count": 5,
    "ori_question": "How should I run an async Python web service?",
    "need_content": true
  }'
```

### Response

```json
{
  "results": [
    {
      "rerank_score": 1.82,
      "title": "Page title",
      "url": "https://example.com/page",
      "snippet": "A short UI-oriented excerpt…",
      "summary": "A longer query-focused summary for a model…",
      "content": "# Page title\n\nPage content as Markdown…",
      "site_name": "example.com",
      "site_icon": "https://example.com/favicon.ico",
      "date": "2026-10-25T12:30:00Z",
      "authority": 3
    }
  ]
}
```

| Field | Type | Meaning |
|---|---|---|
| `rerank_score` | number (0–3) | Ranking score; `1.5` or above marks a high-quality result |
| `title` | string | Page title, untrusted |
| `url` | string | Page URL, untrusted |
| `snippet` | string | Short excerpt aimed at a user interface, untrusted |
| `summary` | string | Extractive summary aimed at a model, untrusted |
| `content` | string | Main content as Markdown (empty unless `need_content` is true), untrusted |
| `site_name` | string or null | Registrable domain of the result |
| `site_icon` | string or null | Conventional favicon URL for the domain |
| `date` | string or null | Publication date, when the provider exposes one |
| `authority` | integer (-1–4) | Authority tier from the configured authority list |

No matches return HTTP 200 with `{"results": []}` — an empty result set is a
successful request, but it is not billable.

### Response headers

| Header | Meaning |
|---|---|
| `X-Providers-Used` | Comma-separated providers that answered this request |
| `X-Content-Untrusted` | Always `true`: every content field is third-party data |
| `X-Request-Id` | Request identifier, also present in the structured logs |

## `POST /v1/tools/search/batch`

Runs up to ten queries with shared `count`, `ori_question`, `localization`, and
`need_content` values.

```bash
curl -sS http://localhost:8000/v1/tools/search/batch \
  -H "Authorization: Bearer $SEARCHRELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"queries":["fastapi background tasks","python asyncio tutorial"],"count":5}'
```

```json
{
  "items": [
    {
      "query": "fastapi background tasks",
      "results": [{"rerank_score": 1.9, "title": "…", "url": "…"}],
      "error": null
    },
    {
      "query": "python asyncio tutorial",
      "results": [],
      "error": {
        "message": "…",
        "type": "invalid_request_error",
        "param": null,
        "code": "downstream_timeout"
      }
    }
  ]
}
```

- One to ten queries; more than ten returns `400`.
- Each query is billed separately, and only a query returning a non-empty
  `results` array is billable.
- A failing query carries its own error object and the other queries still
  return results.
- If every query fails, the endpoint returns the standard error envelope
  instead of a `200` body.

## `POST /v1/tools/fetch`

| Field | Type | Required | Notes |
|---|---|---|---|
| `url` | string | yes | HTTP(S) only, 1–8192 characters, no userinfo, ports 80/443 only |

```bash
curl -sS http://localhost:8000/v1/tools/fetch \
  -H "Authorization: Bearer $SEARCHRELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/article"}'
```

```json
{
  "title": "Article title",
  "content": "# Article title\n\nClean page content as Markdown…"
}
```

The fetch path enforces the full [security model](security.md): public-IP-only
DNS resolution with pinning, redirect revalidation, `robots.txt`, content-type
and size limits, and prompt-injection instruction filtering.

## Errors

Every error uses the same envelope:

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

The SDKs retry `408`, `429`, and `5xx` responses with bounded exponential
backoff, and do not retry other `4xx` responses.

## Billing

| Event | Billable | Price |
|---|---|---|
| Search returning a non-empty `results` array | Yes | $0.75 per 1,000 requests |
| Search returning `{"results": []}` | No | — |
| Fetch returning non-empty `content` | Yes | $0.75 per 1,000 requests |
| Fetch returning empty `content` | No | — |
| Any error response | No | — |
| Batch item | Only the individual queries that returned results | As above |

`count` does not change the price. Internally, balances use integer
micro-US dollars and one billable request costs `750` units.

## Rate limits

| Limit | Default | Scope |
|---|---:|---|
| Search QPS | 100 | Per account |
| Fetch QPS | 25 | Per account |
| Fetch QPS | 5 | Per domain, across all accounts |

Limits are sliding windows. With `SR_REDIS_URL` set, counters are shared
across replicas; without it they are per process.

## Query operators

Operators are parsed out of `query`, translated into the upstream request, and
then enforced again locally on the retrieved candidates.

| Operator | Meaning | Example |
|---|---|---|
| `site:` | Restrict to a domain (subdomains included) | `site:python.org` |
| `-site:` | Exclude a domain | `-site:pinterest.com` |
| `-term` | Exclude a term | `-threading` |
| `-"phrase"` | Exclude an exact phrase | `-"rate limit"` |
| `"phrase"` | Require an exact phrase | `"asyncio.run"` |
| `lang:` | Require matching language metadata | `lang:zh,en` |
| `after:` | Published on or after a date | `after:2025`, `after:2025-03`, `after:2025-03-14` |
| `before:` | Published on or before a date | `before:2025-03` |

Date bounds are inclusive. `after:2025-03` means March 1, 2025 onward;
`before:2025-03` means through March 31, 2025. When a date filter is present,
results **without** a verifiable publication date are excluded rather than
presented as if they satisfied the filter. A candidate whose language metadata
is missing is excluded when `lang:` is present.

## Health endpoints

```bash
curl -i http://localhost:8000/healthz
curl -i http://localhost:8000/readyz
```

| Endpoint | Healthy response | Unhealthy response |
|---|---|---|
| `/healthz` | `200 {"status": "ok"}` | `503 {"status": "unavailable"}` |
| `/readyz` | `200 {"status": "ready"}` | `503 {"status": "not_ready"}` |

Both check that the configured search backend is reachable, so a green
`/healthz` means searches can actually run.
