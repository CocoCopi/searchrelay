# Quickstart

Two supported paths: Docker Compose (the fastest route to a running stack) and
a pip install (the route to a service you manage yourself).

Both need credentials. SearchRelay ships **no default API key and no default
SearXNG secret**, and refuses to start without them.

## 1. Docker Compose (recommended first run)

Requirements: Docker with the Compose plugin, and `openssl` for the key
generation in the snippet below.

```bash
export SEARCHRELAY_API_KEY="sk-$(openssl rand -hex 24)"
export SEARXNG_SECRET="$(openssl rand -hex 32)"

cat > .env <<EOF
SEARXNG_SECRET=$SEARXNG_SECRET
SR_API_KEYS_JSON={"$SEARCHRELAY_API_KEY":"local-demo"}
SR_INITIAL_BALANCES_JSON={"local-demo":100000000}
EOF

docker compose up --build
```

The stack starts three services:

| Service | Port | Purpose |
|---|---:|---|
| `api` | 8000 | SearchRelay |
| `searxng` | 8080 | Your metasearch instance (the corpus provider) |
| `redis` | 6379 | Shared rate limiting and billing |

!!! note "Balances are in micro-US dollars"
    `SR_INITIAL_BALANCES_JSON` takes integer micro-US dollars. `100000000` is
    $100.00, and one billable request costs `750` units ($0.00075).

## 2. Verify the service

```bash
curl -i http://localhost:8000/healthz   # liveness + backend reachability
curl -i http://localhost:8000/readyz    # readiness for traffic
```

Both return `503` when the configured search backend cannot be reached, so a
green `/healthz` means the service can actually search, not merely that the
process is alive.

## 3. Run your first search

```bash
curl -sS http://localhost:8000/v1/tools/search \
  -H "Authorization: Bearer $SEARCHRELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"site:fastapi.tiangolo.com async def","count":3}'
```

The response is a JSON object with a `results` array. Check the headers too —
they tell you which providers answered and that the content is untrusted:

```bash
curl -sS -D - -o /dev/null http://localhost:8000/v1/tools/search \
  -H "Authorization: Bearer $SEARCHRELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"fastapi async","count":1}' | grep -i x-
```

```text
x-providers-used: searxng
x-content-untrusted: true
x-request-id: 88ccf470d28540ebaf9047b01ba436d7
```

## 4. Install without Docker

You need Python 3.11+ and a reachable SearXNG instance. The JSON output format
must be enabled in the SearXNG settings; the repository ships a suitable
configuration at `deploy/searxng/settings.yml`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'

export SEARCHRELAY_API_KEY="sk-$(openssl rand -hex 24)"
export SR_API_KEYS_JSON="{\"$SEARCHRELAY_API_KEY\":\"local-demo\"}"
export SR_INITIAL_BALANCES_JSON='{"local-demo":100000000}'
export SR_UPSTREAM_URL='http://localhost:8080'

.venv/bin/uvicorn app.main:app --reload
```

Interactive API documentation is served at `http://localhost:8000/docs`, and the
OpenAPI 3.1 document at `http://localhost:8000/openapi.json`.

## 5. Add paid providers (optional)

SearXNG alone is enough to start. To federate official provider APIs, set
their standard credential variables and list the providers you want in priority
order:

```bash
export BRAVE_SEARCH_API_KEY="..."   # Brave Search API
export SERPER_API_KEY="..."         # Serper
export TAVILY_API_KEY="..."         # Tavily
export EXA_API_KEY="..."            # Exa

export SR_PROVIDER_PRIORITIES="brave,tavily,serper,exa,searxng"
```

Only providers listed in `SR_PROVIDER_PRIORITIES` are queried. If one of them
is down or unconfigured, the others still answer and the response still
returns results. See [Providers](providers.md).

## 6. Create more keys

The admin CLI writes to the same SQLite ledger the service bills against:

```bash
.venv/bin/searchrelay-admin create-key --account team-a --balance 50000000
.venv/bin/searchrelay-admin list-keys
.venv/bin/searchrelay-admin set-budget --account team-a --balance 10000000
.venv/bin/searchrelay-admin usage
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SearchRelay startup configuration error: SR_API_KEYS_JSON or SR_KEYS_FILE must define at least one API key` | No keys configured | Set `SR_API_KEYS_JSON` or point `SR_KEYS_FILE` at a key file |
| `/healthz` returns `503` | The configured search backend is unreachable | Check `SR_UPSTREAM_URL` and that SearXNG answers `/search?format=json` |
| `402` on every request | The account balance reached zero | `searchrelay-admin set-budget --account <id> --balance <micro-usd>` |
| `429` | Account QPS or per-domain Fetch limit exceeded | Back off, or raise `SR_SEARCH_QPS` / `SR_FETCH_QPS` |
| `403` on a fetch | The URL resolved to a private or metadata address | Expected behavior; see [Security model](security.md) |
| Empty `results` array | The upstream returned nothing for the query | Check the provider directly, then the operator filters in [Ranking](ranking.md) |
