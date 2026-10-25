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

The authority tier is a configurable heuristic in later releases, not an audited trust score. Provider order is a prior, not a quality guarantee. Date freshness depends on a provider exposing a trustworthy publication date, and date-filtered queries exclude results without a verifiable date.
