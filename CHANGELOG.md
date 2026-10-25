# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-10-25

The first public release. SearchRelay is a self-hostable web search and fetch
layer for AI agents: a DeepSeek-compatible API that federates whichever search
providers you configure, extracts clean Markdown, and keeps serving results
when one provider is down.

SearchRelay does not own a web index and does not claim to. It is the layer
between your agent and the corpus you choose to run.

### Added

**Search semantics**

- `rerank_score` normalized to a true 0–3 range, so the documented 1.5
  threshold for a high-quality result is reachable. The formula and a worked
  example are in `docs/ranking.md`.
- Local enforcement of `site:`, `-site:`, `-term`, `-"phrase"`, `"phrase"`,
  and `lang:` after retrieval, so an upstream that ignores an operator cannot
  leak results past the filter.
- `after:` and `before:` filters accepting `YYYY`, `YYYY-MM`, and `YYYY-MM-DD`.
  Bounds are inclusive, and results without a verifiable publication date are
  excluded when a date filter is set.

**Provider federation**

- A `SearchProvider` interface with adapters for Brave Search API, Serper,
  Tavily, Exa, SearXNG, and the optional local index.
- Parallel queries with per-provider timeouts, retry with jitter, circuit
  breakers, per-provider budgets, and automatic failover. Responses carry
  `X-Providers-Used` listing the providers that answered.
- Reciprocal Rank Fusion with canonical-URL deduplication.
- Config-driven provider priorities through `SR_PROVIDER_PRIORITIES` and
  `SR_PROVIDER_CONFIG_FILE`.

**Speed**

- A two-tier cache: an in-memory LRU in front of an optional SQLite tier, with
  TTLs by content type and `ETag`/`Last-Modified` revalidation.
- Request coalescing so concurrent identical requests share one upstream call.
- Parallel page fetching, only when `need_content` is true or the provider
  snippet is too short.
- `POST /v1/tools/search/batch`: up to ten queries, billed one unit per
  successful query.

**Quality**

- Main-content extraction with Trafilatura, so navigation menus, cookie
  banners, and link lists never reach a snippet or a summary.
- PDF to Markdown, charset detection when the origin does not declare one,
  and clean Markdown with preserved tables and code blocks.
- Optional Playwright rendering (experimental, off by default).
- An optional local ONNX cross-encoder that blends into `rerank_score`
  (experimental, off by default, no model shipped). The heuristic stays the
  fallback.
- Authority tiers from an operator-controlled YAML list, with TLD and shape
  heuristics.
- Every response field labeled as untrusted in the OpenAPI document, plus an
  `X-Content-Untrusted: true` response header.

**Local index**

- An optional crawler with `robots.txt` support, per-host politeness delay,
  and the same SSRF policy as the fetch service, indexing user-supplied seed
  domains into SQLite FTS5.
- A local-index provider that participates in fusion and filtering like any
  other source. This is private search over your own sites and docs, not a web
  index.

**Agent integration**

- An MCP server exposing `search` and `fetch` over stdio, with install steps
  for Claude Desktop, Claude Code, Cursor, and Windsurf.
- Official Python and TypeScript SDKs, a Go SDK, a LangChain tool, and a
  LlamaIndex reader.
- A cited-answer agent, a deep-research loop, and a Streamlit demo.

**Operations**

- Prometheus metrics, structured JSON logs with request ids, and optional
  OpenTelemetry tracing.
- Redis-backed rate limiting and billing so several replicas share one budget
  and one quota.
- An admin CLI to create keys, set budgets, and review usage.
- A non-root Docker image with a health check, and a Compose stack with
  SearXNG and Redis.

**Proof**

- A reproducible benchmark harness with a 100-query golden set covering
  factual, news, code, and multilingual queries, reporting nDCG@10, p50/p95
  latency, and failure rate per provider and for the federated mode.
- CI running Ruff, mypy, pytest, the benchmark harness self-test, the
  TypeScript and Go SDK tests, a documentation build, and a Docker build.
- A Locust load test and an SSRF test suite.

**Repository**

- The project is named SearchRelay everywhere: packages, modules, the user
  agent, the database name, and the service identity.
- A README with a one-sentence pitch, a 30-second quickstart, a Mermaid
  architecture diagram, a truthful comparison table that states where the
  project loses to DeepSeek Search, Brave, Tavily, and SearXNG, a benchmark
  summary, and badges.
- A MkDocs Material documentation site with quickstart, API reference,
  providers, ranking, security model, deployment, operations, agent
  integration, local index, architecture, benchmarks, and FAQ pages.
- Apache-2.0 licensing, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, issue and pull request templates, and ten
  good-first-issue tasks written as GitHub issue text.

### Changed

- The environment prefix is now `SR_` to match the project name. The
  pre-release `SF_` prefix still works as a fallback so existing deployments
  keep running, and `SR_` always wins when both are set.
- The Python SDK package is `searchrelay` (was `searchfetch`), the Go module
  is `github.com/CocoCopi/searchrelay/clients/go` (was
  `github.com/searchfetch/searchfetch-go`), and the TypeScript SDK is
  `searchrelay-sdk`.
- The crawl and fetch user agent is `SearchRelayBot/1.0` (was
  `DeepSeekBot/1.0`).
- The version is `0.1.0`; the service reports it in the OpenAPI document and
  in the structured logs.

### Fixed

- `SR_CACHE_DATABASE_PATH` set to an empty string now disables the persistent
  cache tier instead of pointing SQLite at the current directory, which
  raised a 500 on every search. An empty `SR_DATABASE_PATH` or
  `SR_LOCAL_INDEX_PATH` fails at startup with a clear message.
- The cache recovers when its SQLite file is removed underneath a running
  process.

### Security

- The fetch client no longer trusts ambient proxy environment variables.
- Hostnames are resolved before every request and every redirect, and **all**
  resolved addresses must be globally routable. The connection is pinned to
  the validated address while the original `Host` header and TLS SNI hostname
  are preserved.
- Redirects are revalidated, userinfo is rejected, and only ports 80 and 443
  are accepted.
- The service fails fast at startup when no API key is configured, when the
  authority file is malformed, or when the optional reranker is enabled
  without a usable model. There is no default API key and no default SearXNG
  secret.
- `localization` is validated as an IPv4 or IPv6 address.

### Known limitations

These are documented rather than hidden:

- The committed benchmark report is a **synthetic harness self-test**. It
  proves the harness and the metric implementation work over the 100-query
  golden set. It is **not** evidence about Brave, Serper, Tavily, Exa, or
  SearXNG result quality, and no live provider quality numbers are published
  yet.
- The Docker image is built by CI but is not published to a registry by this
  repository.
- The local index is SQLite FTS5, which suits small private corpora rather
  than large-scale crawling.
- The optional cross-encoder reranker has not been benchmarked against the
  heuristic in this repository.
- Ranking depends on provider metadata: freshness needs a trustworthy
  publication date and the language filter needs language metadata. Candidates
  without the relevant metadata are excluded when the matching filter is used.
- SearchRelay does not own a web index and makes no claim about web-wide
  coverage.

[Unreleased]: https://github.com/CocoCopi/searchrelay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CocoCopi/searchrelay/releases/tag/v0.1.0
