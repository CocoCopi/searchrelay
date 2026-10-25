# Deployment

## Docker image

The repository builds a single image that runs the API as a non-root user with
a health check:

```bash
docker build --tag searchrelay:0.1.0 .
docker run --rm -p 8000:8000 \
  -e SR_API_KEYS_JSON='{"sk-example":"demo"}' \
  -e SR_INITIAL_BALANCES_JSON='{"demo":100000000}' \
  -e SR_UPSTREAM_URL='http://host.docker.internal:8080' \
  searchrelay:0.1.0
```

Image properties:

| Property | Value |
|---|---|
| Base image | `python:3.12-slim` |
| Runtime user | `appuser` (uid 10001), non-root |
| Exposed port | 8000 |
| Health check | `GET /healthz` every 20s, 5s timeout, 15s start period |
| State directory | `/data` (billing ledger, cache tier, local index) |
| Optional extras | `[redis]`, `[render]` (Playwright), `[reranker]`, `[tracing]` |

## Compose stack

`docker-compose.yml` starts three services: the API, a SearXNG instance, and
Redis.

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

The SearXNG service substitutes `SEARXNG_SECRET` into
`deploy/searxng/settings.yml` at start-up, so the secret never has to be
committed. The Redis service persists its data in a named volume.

## Configuration

Every setting uses the `SR_` prefix. The full table is in the
[API reference](api.md); the deployment-relevant ones are:

| Variable | Purpose |
|---|---|
| `SR_UPSTREAM_URL` | SearXNG base URL used by the `searxng` provider |
| `SR_PROVIDER_PRIORITIES` | Which providers to query, in priority order |
| `SR_API_KEYS_JSON` | Bearer key → account ID map |
| `SR_INITIAL_BALANCES_JSON` | Account ID → micro-US dollar balance map |
| `SR_KEYS_FILE` | JSON file with `keys` and `balances` instead of the two maps above |
| `SR_DATABASE_PATH` | Billing ledger path |
| `SR_CACHE_DATABASE_PATH` | SQLite cache tier path; empty or unset disables it |
| `SR_REDIS_URL` | Shared Redis for rate limits and billing across replicas |
| `SR_LOG_JSON` | Structured JSON logs (keep `true` in production) |
| `SR_LOG_LEVEL` | Log level, `INFO` by default |
| `SR_METRICS_ENABLED` | Expose `/metrics` |
| `SR_TRACING_ENABLED` | Export OpenTelemetry spans |
| `SR_OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP HTTP endpoint for traces |
| `SR_ALLOWED_DOMAINS_CSV` | Optional fetch allowlist |
| `SR_BLOCKED_DOMAINS_CSV` | Optional fetch blocklist |

The pre-0.1.0 `SF_` prefix still works as a fallback for existing
deployments, but `SR_` always wins when both define the same setting.

## Behind a reverse proxy

The service speaks plain HTTP and expects TLS to be terminated by your proxy.
Forward the `X-Request-Id` header so request identifiers line up across the
proxy and the service logs.

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Request-Id $request_id;
    proxy_read_timeout 120s;
}
```

Keep `/metrics` reachable only from your monitoring network, or remove it with
`SR_METRICS_ENABLED=false`.

## Running several replicas

Two things are process-local by default and become shared when
`SR_REDIS_URL` is set:

| Concern | Without Redis | With Redis |
|---|---|---|
| Account QPS limits | Per process | Shared across replicas |
| Per-domain fetch limit | Per process | Shared across replicas |
| Balance ledger | SQLite file | Redis-backed atomic ledger |
| Response cache | In-memory LRU plus optional SQLite file | Same; put the SQLite tier on a shared volume if replicas should share entries |

!!! note "One ledger per environment"
    Point every replica at the same `SR_DATABASE_PATH` (a shared volume) or
    at the same Redis instance. Splitting the ledger across replicas splits
    the balance.

## Storage and backups

| Path | Contents | Back it up? |
|---|---|---|
| `SR_DATABASE_PATH` | API keys, balances, usage ledger | **Yes** — it is the billing state |
| `SR_CACHE_DATABASE_PATH` | Cached responses | No — it is disposable |
| `SR_LOCAL_INDEX_PATH` | Your private local index | Yes, if you use the local index |

A backup is a file copy of the SQLite database. Take it with the service
stopped, or use SQLite's online backup API; copying a live database file can
produce a torn copy.

## Upgrades

1. Read the [changelog](https://github.com/searchrelay/searchrelay/blob/main/CHANGELOG.md).
2. Back up the billing database.
3. Pull the new image and restart the API.
4. Confirm `/healthz` and `/readyz` return 200 and that one search succeeds.

The HTTP contract is stable within a major version. The v0.1.0 release renamed
the environment prefix from `SF_` to `SR_`; the old names still work as a
fallback, so an upgrade does not require an immediate configuration change.

## Health checks in orchestrators

| Probe | Endpoint | Use |
|---|---|---|
| Liveness | `GET /healthz` | Restart the container if the process is wedged |
| Readiness | `GET /readyz` | Remove the pod from the load balancer while the backend is unreachable |

Because `/healthz` also probes the search backend, a backend outage will mark
every replica unhealthy. That is intentional for a search service, but it
means the orchestrator will restart pods during a provider outage. If you
prefer liveness to mean "the process is alive", use `/readyz` for traffic
gating and monitor `/healthz` as an alert instead of a restart trigger.

## Known deployment limitations

- The image is not published to a registry by this repository; build it
  yourself or fork the workflow.
- The Compose stack uses `searxng/searxng:latest`, which is convenient but not
  reproducible. Pin a digest for production.
- The billing ledger defaults to SQLite, which is a good fit for one or a few
  replicas but is not a multi-region ledger.
- JavaScript rendering requires the `render` extra and a Playwright browser
  image, which the default image does not include.
