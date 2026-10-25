# Architecture

## Implemented API-first architecture

```text
AI agent / RAG
      │
      ▼
FastAPI contract layer
  ├─ request validation
  ├─ bearer authentication
  ├─ account QPS + per-domain limits
  ├─ SQLite balance ledger
  └─ OpenAI-style errors
      │
      ├──────────────► Safe Fetch service
      │                  ├─ URL policy
      │                  ├─ DNS/public-IP validation + pinning
      │                  ├─ redirect revalidation
      │                  ├─ robots.txt
      │                  ├─ byte/type limits
      │                  └─ HTML → clean Markdown
      │
      ▼
Search pipeline
  ├─ operator parser
  ├─ SearXNG backend adapter
  ├─ date/site filtering + dedupe
  ├─ safe page materialization
  ├─ authority/freshness/quality/relevance ranking
  └─ query-focused snippet/summary generation
```

SearXNG is deliberately behind `SearchBackend` in `app/engine.py`. Replacing it does not change the public API, billing logic, clients, or Fetch security boundary.

## Search semantics

The parser recognizes:

- `site:example.com`
- `-site:example.com`
- `-term`
- `-"exact phrase"`
- `"exact phrase"`
- `lang:zh,en`
- `after:YYYY` or `after:YYYY-MM`
- `before:YYYY` or `before:YYYY-MM`
- multiple sites: `"site:a.com OR site:b.com"`

Parsed operators are translated into the upstream query and request parameters. Date constraints are also applied to results with known publication dates. Results without dates are retained because an upstream engine may not expose a reliable date.

`ori_question` is retained separately from the rewritten engine query. It drives sentence relevance when constructing the UI snippet and model summary.

## Ranking

`rerank_score` is normalized to 0–3. The current signals are:

- authority tier: 35%;
- freshness: 20%;
- page/result quality: 25%;
- query relevance: 15%;
- click/engine prior: 5%;
- explicit spam penalty.

Scores of 1.5 or greater are intended to identify high-quality results. The authority tier ranges from -1 (unknown) through 4 (highly authoritative). The implementation uses transparent domain and page heuristics; a production deployment should replace this with a trained authority model and audited tier data.

The upstream engine score is used as a relevance/click prior. There is no click collection endpoint in this API contract.

## Summary generation

The service uses extractive, query-focused summarization so it does not hallucinate facts or require another paid model dependency. It selects and orders source sentences based on query-term overlap, then bounds output to the requested approximate lengths.

A production deployment can replace `app/excerpt.py` with a model-backed summarizer while preserving the response fields. In that design:

1. treat page content as hostile data;
2. use a fixed system prompt that forbids following page instructions;
3. constrain output to the source;
4. cache by normalized URL/content hash;
5. never let model output alter URLs, score, authority, or billing state.

## Safe Fetch design

The SSRF boundary is intentionally stricter than ordinary HTTP clients:

1. Parse and normalize the URL.
2. Reject non-HTTP(S), userinfo, nonstandard ports, control characters, and malformed hosts.
3. Apply explicit domain allow/block policies.
4. Resolve every A/AAAA address and reject the hostname if **any** address is not globally routable.
5. Pin the connection to a validated address, retain the original Host header, and set TLS SNI to the original hostname.
6. Repeat all checks for every redirect.
7. Honor robots.txt, content type, body size, and time limits.
8. Remove active/hidden HTML and common prompt-override instructions before returning Markdown.

`httpx` is created with `trust_env=False` so ambient proxy variables cannot bypass the policy.

## Replacing SearXNG with a private corpus

A production-owned corpus should be split into independently scalable planes.

### 1. Crawler

- Seed from Common Crawl and curated/trending feeds.
- Identify as `DeepSeekBot/1.0` and obey robots.txt.
- Use a per-site crawl quota and host-level politeness delay.
- Store raw response metadata separately from extracted content.
- Use a frontier with retry budgets, circuit breakers, and per-tenant backpressure.
- Prefer sitemaps, RSS, feeds, and hyperlink discovery for cheap recency.
- Keep crawl snapshots with fetch timestamp and content hash.

### 2. Content pipeline

- Parse HTML with a hardened parser in an isolated worker.
- Extract main content, language, title, headings, entities, media metadata, and outbound links.
- Normalize Unicode and canonical URLs.
- Detect mirror clusters using title/body shingles and canonical/link signatures.
- Downrank or remove:
  - generated doorway pages and near-duplicate site farms;
  - keyword-stuffed pages and link/redirect schemes;
  - scraped feeds with no original value;
  - pages whose primary purpose is manipulating an answer engine;
  - low-information pages generated at scale.
- Store image/video embeddings separately when multimodal retrieval is required.
- Keep provenance and license metadata for every document.

### 3. Index

Use separate indexes by language and modality. A practical design is:

- lexical BM25/FTS for exact phrases and rare terms;
- dense multilingual vector retrieval for semantic matches;
- link/authority graph features;
- freshness and duplicate-cluster features;
- an ANN store for vectors and a columnar document store for metadata.

Partition by crawl snapshot/language and use a hot/cold tier. Hundreds of billions of pages require sharding, object storage, incremental builds, and a query planner that selects only a small set of partitions.

### 4. Query planner

1. Parse operators and detect intent/entity/time constraints.
2. Rewrite using `ori_question` without changing explicit operators.
3. Expand terminology and translations.
4. Weight exact phrases, rare terms, entities, and negative terms.
5. Select language/index partitions.
6. Run lexical and dense retrieval in parallel.
7. Merge candidates, apply date/site/security filters, and fetch missing metadata.

### 5. Reranking

Train a reranker over:

- authority/domain trust;
- publication and crawl freshness;
- original-content and spam probability;
- query relevance;
- result diversity;
- historical click behavior and dwell time.

Click data must be privacy-preserving, account-scoped where appropriate, and treated as a biased prior rather than truth. Emit the same 0–3 score and -1..4 authority tier so the public contract does not change.

### 6. Summarization

Summarize only after final ranking, using cached model output where possible. Summary generation should have independent time and size budgets so a slow model cannot cause the whole Search request to time out. Keep the summary faithful to source text and retain citations.

## Deployment and rate-limit scaling

The included limiter is process-local and the ledger is SQLite-backed. That is appropriate for the one-process Docker deployment and tests. A multi-instance deployment should replace them with:

- a shared Redis-compatible sliding-window/token-bucket limiter;
- an atomic ledger or billing service with idempotency keys;
- a distributed cache for robots decisions, page extraction, and summaries;
- an admission queue that protects downstream search engines;
- per-tenant and per-domain fairness controls.

The public behavior—independent account Search/Fetch QPS and global per-domain Fetch throttling—can remain unchanged.
