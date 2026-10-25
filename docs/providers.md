# Search providers

SearchRelay is an orchestration and retrieval layer. It does **not** own, crawl, or claim to rank a web index. It queries search providers you configure and combines their responses.

## Supported adapters

| Provider | Integration | Credentials | Notes |
|---|---|---|---|
| SearXNG | User-run self-hosted instance | `SR_UPSTREAM_URL` | JSON API; the operator controls routing, engines, rate limits, and compliance. |
| Brave Search API | Official JSON API | `BRAVE_SEARCH_API_KEY` | Uses the official Brave Search endpoint; a Brave subscription and accepted API terms are required. |
| Serper | Official Serper JSON API | `SERPER_API_KEY` | Uses Serper's API rather than scraping Google HTML. Serper and underlying engine terms still apply. |
| Tavily | Official JSON API | `TAVILY_API_KEY` | Uses Tavily's official search endpoint. |
| Exa | Official JSON API | `EXA_API_KEY` | Uses Exa's official search endpoint. |
| Local index | Optional private corpus | `SR_LOCAL_INDEX_PATH` | Searches only user-supplied sites and documents; it is not a web index. See [`local-index.md`](local-index.md). |

SearchRelay never scrapes Google, Bing, or Brave HTML result pages.

## Terms-of-service tradeoff

Official APIs are the supported path for commercial providers. Their pricing, quotas, result licensing, retention, and acceptable-use terms are controlled by those vendors. A deployment is responsible for reviewing and accepting the terms of every enabled provider.

A self-hosted SearXNG instance can reduce vendor lock-in and API cost, but it does not remove upstream obligations. Depending on the engines and websites an operator enables, automated requests may be restricted by engine terms, rate limits, `robots.txt`, or site policies. SearchRelay never presents a SearXNG deployment as permission to scrape a prohibited source. Operators remain responsible for the engines and configuration they choose.

## Enabling providers

Without a provider configuration file, only SearXNG is enabled:

```bash
export SR_UPSTREAM_URL='http://searxng:8080'
export SR_PROVIDER_PRIORITIES='searxng'
export SR_API_KEYS_JSON='{"your-generated-api-key":"account-id"}'
```

To federate official APIs, set their key environment variables and list the providers in priority order:

```bash
export BRAVE_SEARCH_API_KEY='...'
export SERPER_API_KEY='...'
export TAVILY_API_KEY='...'
export EXA_API_KEY='...'
export SR_PROVIDER_PRIORITIES='searxng,brave,serper,tavily,exa'
```

For budgets and provider-specific settings, create a JSON file and set `SR_PROVIDER_CONFIG_FILE`:

```json
{
  "priorities": ["searxng", "brave", "serper"],
  "providers": {
    "searxng": {
      "enabled": true,
      "base_url": "http://searxng:8080",
      "timeout_seconds": 8,
      "requests_per_minute": 120,
      "max_concurrency": 4,
      "max_results": 30
    },
    "brave": {
      "enabled": true,
      "api_key_env": "BRAVE_SEARCH_API_KEY",
      "timeout_seconds": 3,
      "requests_per_minute": 60,
      "max_concurrency": 2,
      "max_results": 20
    },
    "serper": {
      "enabled": true,
      "api_key_env": "SERPER_API_KEY",
      "timeout_seconds": 3,
      "requests_per_minute": 60,
      "max_concurrency": 2,
      "max_results": 20
    }
  }
}
```

`priorities` determines provider order and deterministic tie-breaking. Only listed providers are queried. A listed provider with `"enabled": false` is skipped. Enabling an official provider without its key causes startup to fail with the missing environment-variable name.

Supported provider names are `searxng`, `brave`, `serper`, `tavily`, `exa`, and `local`.

## Per-provider budgets

Each provider has independent limits:

- `requests_per_minute` is a sliding-window budget for provider searches, including retry attempts.
- `max_concurrency` limits simultaneous upstream calls for that provider.
- `max_results` caps candidates accepted from that provider before fusion.
- `timeout_seconds` applies to each provider attempt.

Global resilience settings are configured with `SR_PROVIDER_RETRY_ATTEMPTS`, `SR_PROVIDER_RETRY_BASE_DELAY_SECONDS`, `SR_PROVIDER_CIRCUIT_FAILURE_THRESHOLD`, and `SR_PROVIDER_CIRCUIT_RESET_SECONDS`.

A circuit opens after the configured number of failed logical provider calls. While open, calls fail fast until the reset interval; then one half-open probe is allowed. Retries use bounded exponential backoff with full jitter.

## Fusion and deduplication

Providers are queried concurrently. SearchRelay applies Reciprocal Rank Fusion (RRF):

```text
rrf_score(url) = Σ 1 / (k + provider_rank)
```

The default is `k = 60`. URLs are normalized for deduplication by lower-casing scheme and host, removing fragments and common tracking parameters, sorting query parameters, and removing a trailing slash. The first provider's original URL and metadata are retained while the combined provider list and RRF score are used internally for ordering.

Local operator enforcement still runs after fusion. Provider success therefore cannot bypass `site:`, negative term/phrase, required phrase, language, or date checks.

## Failure behavior

A failed provider does not remove successful results from other providers. Successful responses include:

```http
X-Providers-Used: searxng,brave
```

The header lists providers that answered successfully, including a provider that returned an empty result set. It omits failed providers. If every configured provider fails, Search returns the existing HTTP 504 error envelope with code `downstream_timeout`; an invalid upstream payload retains the HTTP 500 `upstream_error` semantics.

The optional `localization` IP address is forwarded to SearXNG, which supports `remote_ip`. The official adapters do not expose a raw-IP localization parameter, so they receive the query without pretending to honor that signal.

## Provider limitations

Provider result schemas differ. In particular, not every official API exposes a reliable result language or publication date. Local `lang:` and date filtering intentionally rejects results without verifiable metadata rather than trusting the provider. This can reduce recall but keeps operator semantics enforceable.
