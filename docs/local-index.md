# Optional local index

SearchRelay can search **your own sites and documents** through a small local
index. This is private search over a corpus you control — it is not a web
index, and SearchRelay does not claim to own or operate one.

## What it does

* A polite crawler fetches the seed sites you list, obeys `robots.txt`, waits
  between requests to the same host, and identifies itself as
  `SearchRelayBot/1.0`.
* The main content of each page is stored in a local SQLite FTS5 database.
* The `local` provider plugs that index into the normal federation, so local
  results are fused with the other providers and filtered by the same
  operators (`site:`, `lang:`, `after:`/`before:`, and the rest).

## What it does not do

* It does not crawl the public web, discover sites on its own, or provide
  coverage of anything you did not explicitly seed.
* It does not bypass `robots.txt` or the rate limits of the sites it visits.
* It does not index pages behind authentication. Point the crawler at pages
  that are publicly reachable.

## Crawl a site

```bash
.venv/bin/python -m app.local_index.crawl \
  --seeds seeds.txt \
  --index data/local-index.db \
  --max-pages 100 \
  --max-depth 2 \
  --delay 1.0
```

`seeds.txt` contains one seed URL or domain per line:

```text
# my own documentation
docs.example.com
https://blog.example.com/posts
```

Useful flags:

| Flag | Default | Meaning |
|---|---:|---|
| `--seed` | – | A seed URL or domain; repeatable |
| `--seeds` | – | File with one seed per line |
| `--index` | `data/local-index.db` | SQLite database for the index |
| `--max-pages` | `50` | Maximum number of pages to fetch |
| `--max-depth` | `2` | Link depth from the seeds |
| `--delay` | `1.0` | Per-host politeness delay in seconds |
| `--user-agent` | `SearchRelayBot/1.0` | Crawler user agent |
| `--ignore-robots` | off | Only for sites you own and control |

The crawler reuses the API's SSRF policy: private, loopback, link-local, and
cloud-metadata destinations are refused, DNS results are pinned to the
validated address, and every redirect is revalidated.

## Enable the provider

```bash
export SF_PROVIDER_PRIORITIES='local,searxng'
export SF_LOCAL_INDEX_PATH='data/local-index.db'
```

Or pin the path in the provider configuration file:

```json
{
  "priorities": ["local", "searxng"],
  "providers": {
    "local": {
      "enabled": true,
      "base_url": "/srv/searchrelay/local-index.db",
      "max_results": 20
    }
  }
}
```

When `local` is enabled, the `X-Providers-Used` header tells you whether it
answered a request.

## Extending the index

The store is a normal SQLite database with an FTS5 table:

```sql
SELECT d.url, d.title, bm25(documents_fts) AS score
FROM documents_fts
JOIN documents d ON d.id = documents_fts.rowid
WHERE documents_fts MATCH '"deployment"';
```

You can add documents yourself by inserting into `documents` (the FTS index is
kept in sync by triggers) or by calling `LocalIndex.upsert()` from Python.
The schema is documented in `app/local_index/store.py`.

## Limitations

* The crawler is intentionally small: it is a breadth-first fetch of the
  seeds you provide, not a production-grade crawler with scheduling,
  incremental refresh, or content deduplication.
* Pages that require JavaScript are indexed with their static content unless
  JavaScript rendering is enabled.
* The index stores the extracted Markdown, not the original bytes, so
  re-crawling is the only way to refresh it.
* Search quality over the local index is plain BM25 lexical matching. The
  ranking layer still applies the normal authority, freshness, and relevance
  heuristics.
