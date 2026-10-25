# FAQ

## Does SearchRelay own a web index?

No, and it does not claim to. SearchRelay is a layer that queries corpus
providers you configure: official provider APIs (Brave, Serper, Tavily, Exa), a
SearXNG instance you run, and an optional private index of your own sites. The
optional local index is explicitly *private search over your own sites and
docs*, not a web index.

## Is it compatible with the DeepSeek Search API?

Yes. `POST /v1/tools/search` and `POST /v1/tools/fetch` keep the same request
fields, the same response fields, the same error envelope, and the same
billing semantics (a Search is billable only when it returns a non-empty
`results` array; a Fetch only when it returns non-empty `content`). See the
[API reference](api.md).

## What does it cost?

Nothing to install and nothing to run: the code is Apache-2.0 licensed. What
you pay is for the corpus you choose. SearXNG is free to run but the engines
it queries have their own terms and may rate-limit or block you. Brave, Serper,
Tavily, and Exa are paid APIs with their own pricing. The service's internal
accounting is $0.75 per 1,000 successful requests, which exists to make
multi-tenant deployments manageable, not to charge you.

## Which providers can I use?

Any provider you can write an adapter for. Six ship in the box: Brave Search
API, Serper, Tavily, Exa, SearXNG, and the local index. They implement one
`SearchProvider` interface, so adding another does not change the public API,
the billing rules, the clients, or the fetch security boundary. See
[Providers](providers.md).

## What happens when a provider is down?

The request still succeeds with the results from the providers that did
answer, and the `X-Providers-Used` header tells you who contributed. Failed
providers are retried with jitter, their circuit opens after repeated
failures, and per-provider budgets stop a provider from consuming the whole
request budget. A provider that returns nothing is not an error.

## Does SearchRelay scrape Google, Bing, or Brave?

No. SearchRelay only calls official APIs and a SearXNG instance that you run.
A SearXNG instance queries the engines you configure it with, and the terms,
rate limits, and legal exposure of those engines are yours to accept. See the
terms-of-service section of [Providers](providers.md).

## Are operators enforced, or just passed through?

Both. Operators are translated into the upstream request, and then enforced
again locally on the retrieved candidates. If a provider ignores `site:` or
`-term`, the local filter removes the result anyway. When a date filter is
present, results without a verifiable publication date are excluded instead
of being presented as if they matched.

## How is `rerank_score` calculated?

It is a transparent heuristic over five normalized signals — authority (35%),
freshness (20%), quality (25%), relevance (15%), and a click prior (5%) —
multiplied by three, minus an explicit spam penalty, clamped to 0–3. The
formula and a worked example are in [Ranking](ranking.md). It is not a claim
about global web authority, and the authority tier itself is a configurable
YAML list rather than an audited trust score.

## Is the content safe to hand to a model?

It is **untrusted data**, and SearchRelay labels it as such. The service
filters common prompt-override instructions, labels every response field as
untrusted in the OpenAPI document, and sets `X-Content-Untrusted: true`. No
filter can guarantee safety, so keep retrieved content inside explicit document
delimiters and tell the model never to follow instructions found in retrieved
content. See [Security model](security.md).

## Can I run it entirely offline?

Partly. The service, the ranking, the cache, and the local index work without
internet access, and the local index lets you search your own sites offline.
General web coverage needs either a reachable SearXNG instance or a paid
provider API.

## Do I need Docker?

No. `pip install -e .` and `uvicorn app.main:app` are enough; Docker Compose
is simply the fastest way to get a SearXNG instance and Redis alongside the
API. See [Quickstart](quickstart.md).

## What is experimental?

- **Playwright rendering** (`SR_RENDER_JS`): off by default, and the default
  image ships no browser.
- **ONNX cross-encoder reranking** (`SR_RERANK_CROSS_ENCODER_ENABLED`): off by
  default, no model is shipped, and the heuristic remains the fallback.
- **OpenTelemetry tracing** (`SR_TRACING_ENABLED`): off by default.
- **The local index**: optional and aimed at small, private corpora.

Each is documented as experimental wherever it appears.

## The benchmark numbers look too good. Are they real?

They are real numbers from a **synthetic harness self-test**, which is not the
same thing as real provider quality. The committed table proves the harness and
the metric implementation work over all 100 golden-set queries; it says
nothing about Brave, Serper, Tavily, Exa, or SearXNG result quality. Live
numbers require live credentials and are recorded with the exact command and
date. See [Benchmarks](benchmarks.md).

## Why did the environment variables change from `SF_` to `SR_`?

The project was renamed to SearchRelay before the v0.1.0 release, and the
environment prefix follows the project name. The old `SF_` names still work as
a fallback so existing deployments keep running; `SR_` always wins when both
are set.

## How do I create API keys and manage budgets?

```bash
searchrelay-admin create-key --account team-a --balance 50000000
searchrelay-admin set-budget --account team-a --balance 10000000
searchrelay-admin list-keys
searchrelay-admin usage
```

The CLI writes to the same ledger the service bills against. See
[Operations](operations.md).

## What are the known limitations?

- No live provider quality benchmark is published yet.
- The Docker image build is verified by CI, but this repository does not
  publish images to a registry.
- The local index is SQLite FTS5, which suits small private corpora rather
  than large-scale crawling.
- The cross-encoder reranker has not been benchmarked against the heuristic in
  this repository.
- Ranking depends on provider metadata: freshness needs a trustworthy
  publication date, and the language filter needs language metadata. Candidates
  without those metadata are excluded when the corresponding filter is used.

## How can I help?

Read [CONTRIBUTING.md](https://github.com/CocoCopi/searchrelay/blob/main/CONTRIBUTING.md)
and pick up one of the tasks in
[Development](good-first-issues.md). Bug reports, provider adapters, and
documentation fixes are all welcome.
