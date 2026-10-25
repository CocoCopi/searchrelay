# Ranking

SearchRelay returns `rerank_score` on an inclusive **0–3** scale. The score is a transparent heuristic over result metadata; it is not a claim about global web authority and SearchRelay does not own or rank a web index.

## Formula

Each component is normalized to the inclusive range 0–1:

- `A` — authority: `max(authority_tier, 0) / 4`
- `F` — freshness: newer publication dates score closer to 1
- `Q` — quality: title, snippet, content length, and simple domain-shape heuristics
- `R` — relevance: query-term coverage in title/snippet/content, optionally raised by a normalized provider score
- `P` — click prior: a conservative prior based on provider score and title/query overlap

The weighted score is:

```text
W = 0.35 × A + 0.20 × F + 0.25 × Q + 0.15 × R + 0.05 × P
```

The weights sum to `1.0`, so `W` is in 0–1. The public score is:

```text
rerank_score = round(clamp(3 × W - spam_penalty, 0, 3), 3)
```

`spam_penalty` is expressed directly in final-score points. This keeps the score bounded while allowing a sufficiently strong result to exceed the high-quality threshold of `1.5`. Before a spam penalty, reaching `1.5` requires `W >= 0.5`.

## Authority tiers

The authority component comes from an operator-controlled YAML file (default `config/authority.yaml`):

```yaml
version: 1
tiers:
  4: [who.int, un.org]
  3: [nature.com]
tld_tiers:
  gov: 4
  edu: 3
  org: 2
```

A host matches a tier entry when it equals the domain or is a subdomain of it, so `news.nature.com` matches `nature.com`. When no domain entry matches, the last label is looked up in `tld_tiers`. Domains that match nothing fall back to shape heuristics (user-hosted and spam-looking domains score 0, unknown domains score -1).

To extend the list, copy `config/authority.yaml`, add your domains, and point `SF_AUTHORITY_CONFIG_FILE` at the copy. A malformed file makes the service refuse to start with a clear message instead of silently ranking with the defaults.

The tier is a heuristic label, not an audited trust score.

## Optional cross-encoder reranking

A local ONNX cross-encoder can refine the score. It is **off by default** and experimental: no model is shipped with the project, and the operator supplies the model directory.

```bash
export SF_RERANK_CROSS_ENCODER_ENABLED=true
export SF_RERANK_CROSS_ENCODER_MODEL=/opt/models/ms-marco-MiniLM
export SF_RERANK_CROSS_ENCODER_WEIGHT=0.35
```

The model directory must contain `model.onnx` and `tokenizer.json`, and the optional `reranker` extra must be installed (`pip install 'searchrelay-api[reranker]'`).

The heuristic score stays the fallback. When the reranker is disabled, when the model cannot be loaded at startup (the service then refuses to start with a clear message), or when scoring fails at runtime, the heuristic score is used unchanged.

With a reranker logit `z`, the final score is:

```text
cross_points = 3 × sigmoid(z)
rerank_score = round(clamp((1 - w) × heuristic + w × cross_points, 0, 3), 3)
```

where `w` is `SF_RERANK_CROSS_ENCODER_WEIGHT` (default `0.35`).

## Worked example

Consider a result with:

| Component | Value | Weighted contribution |
|---|---:|---:|
| Authority tier | 4 (`A = 1.0`) | 0.350 |
| Freshness | 1.0 | 0.200 |
| Quality | 0.9 | 0.225 |
| Relevance | 1.0 | 0.150 |
| Click prior | 1.0 | 0.050 |

Then:

```text
W = 0.350 + 0.200 + 0.225 + 0.150 + 0.050
  = 0.975

rerank_score = round(3 × 0.975, 3)
             = 2.925
```

A result with all five component values at 0 receives `0.0`; a result with all five at 1 receives `3.0`.

## Limitations

The authority tier is a configurable heuristic, not an audited trust score. Provider order is a prior, not a quality guarantee. Date freshness depends on a provider exposing a trustworthy publication date, and date-filtered queries exclude results without a verifiable date. The optional cross-encoder has not been benchmarked against the heuristic in this repository; it is exposed for operators who want to supply and validate their own model.
