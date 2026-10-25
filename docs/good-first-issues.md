# Development: good first issues

Ten tasks that are small enough to finish in an afternoon and useful enough to
matter. Each one is written as GitHub issue text — copy the body into a new
issue and open it, or pick one up directly and open a pull request.

If you claim a task, comment on the issue so two people do not duplicate the
work. If you finish one that is not listed here, open an issue describing it
and label it `good first issue` — please include the same structure: problem,
proposed change, acceptance criteria, and out-of-scope notes.

---

## 1. Add a Kagi Search API adapter

**Labels:** `good first issue`, `provider`, `enhancement`

**Body:**

Kagi has an official Search API with JSON responses, so it fits the existing
`SearchProvider` interface without special handling. SearchRelay currently
ships Brave, Serper, Tavily, Exa, SearXNG, and the local index.

**Proposed change**

Add a `KagiProvider` in `app/providers/adapters.py` that implements the
`SearchProvider` interface, maps the Kagi response into the shared result
model, and registers itself in `app/providers/factory.py` behind the
`kagi` provider name. Read the credential from the `KAGI_API_KEY` environment
variable, following the same pattern as `BRAVE_SEARCH_API_KEY` and
`SERPER_API_KEY`.

**Acceptance criteria**

- `SR_PROVIDER_PRIORITIES=kagi,searxng` queries Kagi.
- `KAGI_API_KEY` missing means the provider is skipped, not that the request
  fails.
- A Kagi timeout or `5xx` is handled by the existing retry and circuit-breaker
  logic, and other providers still answer.
- Unit tests in `tests/test_providers.py` cover the success path, the missing
  credential, and the failure path using the existing HTTP mocking helpers.
- `docs/providers.md` lists Kagi with its credential variable and terms-of-service
  note.

**Out of scope**

Kagi's Summarizer API, the FastGPT API, and any non-official scraping of
kagi.com.

---

## 2. Add a SerpAPI adapter

**Labels:** `good first issue`, `provider`, `enhancement`

**Body:**

SerpAPI is a common alternative to Serper and returns a single JSON payload
for several engines. It is a good second adapter to add because the response
shape differs from Serper's, which proves the result model is provider-neutral.

**Proposed change**

Add a `SerpApiProvider` in `app/providers/adapters.py` that queries the
SerpAPI endpoint with an API key from `SERPAPI_API_KEY`, and maps
`organic_results` (and, if easy, `news_results`) into the shared result
model. Register it as `serpapi` in `app/providers/factory.py`.

**Acceptance criteria**

- The adapter sets `engine` and `publishedDate` from the upstream payload when
  they are present, and leaves them unset otherwise.
- Rate-limit responses from SerpAPI do not crash the federation; they count
  as a provider failure and other providers still answer.
- Tests in `tests/test_providers.py` cover a populated response, an empty
  response, and an error response.
- `docs/providers.md` documents the credential variable and pricing note.

**Out of scope**

Support for SerpAPI's many optional parameters, and SerpAPI's image, maps, or
shopping verticals.

---

## 3. Add a Wikipedia (MediaWiki) API adapter

**Labels:** `good first issue`, `provider`, `enhancement`

**Body:**

Wikipedia's official API is free, needs no credential, and is a useful
provider for factual queries — the golden set has a factual slice that would
benefit from a stable, citation-friendly source.

**Proposed change**

Add a `WikipediaProvider` that queries the MediaWiki `list=search` endpoint
with `format=json`, maps the result titles and snippets into the shared result
model, and builds a URL of the form
`https://en.wikipedia.org/wiki/<title>`. Register it as `wikipedia` in
`app/providers/factory.py`. The language should follow the `lang:` operator
when present.

**Acceptance criteria**

- No credential is required, and the provider is skipped when it is not listed
  in `SR_PROVIDER_PRIORITIES`.
- Titles containing spaces or non-ASCII characters produce correctly
  percent-encoded URLs.
- The provider respects the per-provider timeout and budget settings like
  every other adapter.
- Tests cover a normal response, an empty response, and a malformed payload.
- `docs/providers.md` documents the provider and states that the API is
  subject to the Wikimedia Terms of Use.

**Out of scope**

Wikipedia full-text search quality tuning, language edition fallback, and
article content extraction.

---

## 4. Add an async Python client

**Labels:** `good first issue`, `sdk`, `enhancement`

**Body:**

The Python SDK is synchronous only. Agents that make many concurrent searches
would benefit from an async client, and the SDK already depends on `httpx`,
which supports async natively.

**Proposed change**

Add `AsyncSearchRelayClient` to `clients/python/searchrelay/client.py` with
`async` counterparts of `search`, `search_batch`, and `fetch`, opening an
`httpx.AsyncClient` internally and supporting `async with`. Keep the existing
synchronous client unchanged so current users are not affected.

**Acceptance criteria**

- The async client reuses the same request construction, retry policy, and
  error decoding as the synchronous one — no duplicated parsing logic.
- `async with AsyncSearchRelayClient(...) as client:` works, and the client
  can also be used without the context manager.
- `tests/test_client.py` covers a successful search, an error response, and a
  retry on a retryable status.
- The README and `clients/python` documentation show a short async example.

**Out of scope**

Async variants of the LangChain tool and the LlamaIndex reader.

---

## 5. Add batch search to the Go client

**Labels:** `good first issue`, `sdk`, `enhancement`

**Body:**

The service supports `POST /v1/tools/search/batch`, and the TypeScript client
already calls it. The Go client does not, so Go users have to loop themselves
and lose the per-item error handling.

**Proposed change**

Add a `SearchBatch` method to `clients/go/searchrelay/client.go` that posts to
`/v1/tools/search/batch` and returns a slice of per-query items, each carrying
either results or an error.

**Acceptance criteria**

- The method mirrors the Python and TypeScript clients: same request fields,
  same response shape, same retry behavior for retryable statuses.
- Per-item errors are returned as data, not as a single failure for the whole
  batch.
- `clients/go/searchrelay/client_test.go` covers the success path and a
  partially failing batch.
- The Go example in the README shows the new method.

**Out of scope**

Streaming batch results, and concurrency inside the Go client.

---

## 6. Add a `doctor` command to the admin CLI

**Labels:** `good first issue`, `cli`, `enhancement`

**Body:**

Operators regularly hit configuration mistakes that only show up as a failed
request. A diagnostic command would surface them immediately.

**Proposed change**

Add a `searchrelay-admin doctor` subcommand that checks, in order: the
resolved configuration (keys present, balance readable), the billing database
path (writable), the search backend reachability (a single request to the
configured upstream), the cache tier (readable/writable), and Redis
connectivity when `SR_REDIS_URL` is set. Print one line per check with
`ok`, `warn`, or `fail`, and exit non-zero if any check fails.

**Acceptance criteria**

- The command never prints a key or a secret, only whether it is configured.
- A failing check names the setting to fix and the action to take.
- The command exits `0` when every check passes and `1` otherwise.
- Tests in `tests/test_admin_cli.py` cover the success path and at least one
  failing check using the existing CLI test helpers.
- `docs/operations.md` documents the command.

**Out of scope**

Automatic remediation, and a web dashboard for the checks.

---

## 7. Publish a Grafana dashboard for the built-in metrics

**Labels:** `good first issue`, `ops`, `enhancement`

**Body:**

The service already exposes Prometheus metrics, but there is no dashboard to
look at them with. A checked-in dashboard JSON makes the metrics immediately
usable.

**Proposed change**

Add a `deploy/grafana/searchrelay-dashboard.json` file with panels for search
and fetch request rate, latency percentiles, error rate by status code,
provider usage, cache hit ratio, and the current billing balance per account
where the metric allows it.

**Acceptance criteria**

- The dashboard imports into Grafana without manual edits.
- Every metric name in the dashboard matches a metric actually exposed by
  `app/metrics.py`; the dashboard must not reference metrics that do not
  exist.
- `docs/operations.md` explains how to import the file and how to point
  Grafana at the service.
- A test asserts that every metric name used in the dashboard JSON exists in
  the metrics module, so the dashboard cannot silently drift.

**Out of scope**

Alerting rules and a Grafana provisioning stack.

---

## 8. Report failed providers in a response header

**Labels:** `good first issue`, `observability`, `enhancement`

**Body:**

`X-Providers-Used` tells a caller which providers answered, but not which
failed. During a provider outage, that missing half of the picture makes
debugging harder than it needs to be.

**Proposed change**

Add an `X-Providers-Failed` response header listing the providers that were
tried and did not return usable results, using the same comma-separated
format as `X-Providers-Used`. Set it on search and batch responses.

**Acceptance criteria**

- The header is present on successful responses, including when every provider
  succeeded (in which case it is empty or absent — document which).
- The header never leaks exception text, URLs, or credentials; it contains
  provider names only.
- The OpenAPI document describes both headers.
- Tests in `tests/test_providers.py` cover the all-success, partial-failure,
  and all-failure cases.

**Out of scope**

Per-provider error codes in the response body, and a change to the error
envelope.

---

## 9. Discover pages through `sitemap.xml` in the local index crawler

**Labels:** `good first issue`, `crawler`, `enhancement`

**Body:**

The crawler currently discovers pages by following links. Reading
`sitemap.xml` and `sitemap index` files is the polite, standard way to find
the pages a site actually wants indexed, and it reduces crawl traffic.

**Proposed change**

In `app/local_index/crawler.py`, look for `/sitemap.xml` (and the sitemap
index it points at) when seeding a host, and enqueue the URLs listed there
before falling back to link discovery.

**Acceptance criteria**

- Sitemap fetching goes through the same SSRF policy and `robots.txt`
  handling as any other fetch.
- Sitemap entries respect the existing per-host politeness delay and the
  seed-domain boundary: only URLs inside the seeded domains are enqueued.
- The gzip-encoded sitemaps that many sites serve are handled.
- A malformed or unreachable sitemap is logged and ignored; it must not fail
  the crawl.
- Tests use a local fixture sitemap, including one with a sitemap index and
  one that is malformed.

**Out of scope**

Parsing robots.txt `Sitemap:` directives, incremental crawling, and
recrawl scheduling.

---

## 10. Validate responses against the OpenAPI schema in the test suite

**Labels:** `good first issue`, `testing`, `enhancement`

**Body:**

`openapi.json` is generated by `scripts/export_openapi.py`, and a test checks
that it is up to date. Nothing yet checks that the responses the service
actually produces match the schema it publishes, so a contract drift could
ship unnoticed.

**Proposed change**

Add a test module that validates representative responses — a search result,
an empty search, a batch item, a fetch result, and each error envelope —
against `openapi.json` using a JSON Schema validator.

**Acceptance criteria**

- The schema validator is added as a development dependency in
  `pyproject.toml` and imported with `ignore_missing_imports` handled by
  mypy settings.
- The test validates against the committed `openapi.json`, not a
  freshly generated one, so a stale document fails the test.
- Every documented response field appears in the schema with the right type,
  including the nullable fields.
- The test fails with a readable message naming the offending path.

**Out of scope**

Validating request bodies against the schema, and generating client code from
the schema.
