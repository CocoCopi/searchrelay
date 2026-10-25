# Operations

SearchRelay is designed to run as one or more stateless replicas behind your
own load balancer. This page covers metrics, logs, traces, the shared Redis
backends, the admin CLI, and the container images.

## Metrics

Prometheus metrics are exposed on `GET /metrics` (not part of the public JSON
contract). Scrape it directly:

```yaml
scrape_configs:
  - job_name: searchrelay
    static_configs:
      - targets: ["searchrelay:8000"]
```

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `searchrelay_http_requests_total` | counter | `method`, `route`, `status` | Requests handled |
| `searchrelay_http_request_duration_seconds` | histogram | `method`, `route` | Request latency |
| `searchrelay_provider_requests_total` | counter | `provider`, `outcome` | Provider calls (`success`, `error`, `timeout`, `budget_exhausted`) |
| `searchrelay_provider_request_duration_seconds` | histogram | `provider` | Provider latency |
| `searchrelay_cache_hits` / `_misses` / `_memory_hits` / `_disk_hits` / `_stores` | gauge | – | Live cache counters |
| `searchrelay_billing_charges_total` | counter | `action`, `result` | Billing attempts |
| `searchrelay_rate_limit_rejections_total` | counter | `scope` | Rate-limited requests |
| `searchrelay_build_info` | gauge | `version`, `python_version` | Build metadata |

Metrics are per process. Aggregate them across replicas in Prometheus.

## Structured logs

Logs are JSON lines by default, one object per line:

```json
{"timestamp":"2026-10-25T19:33:27.412Z","level":"info","logger":"searchrelay.http","message":"http_request","request_id":"a1b2...","method":"POST","path":"/v1/tools/search","route":"/v1/tools/search","status":200,"duration_ms":41.882}
```

Every response carries an `X-Request-Id` header. The value is taken from the
incoming `X-Request-Id` header when present, otherwise a new identifier is
generated.

| Variable | Default | Purpose |
|---|---|---|
| `SR_LOG_JSON` | `true` | Emit JSON lines instead of plain text |
| `SR_LOG_LEVEL` | `INFO` | Root log level |

## Tracing

OpenTelemetry tracing is off by default. Enable it with:

```bash
SR_TRACING_ENABLED=true
SR_OTEL_SERVICE_NAME=searchrelay
SR_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
```

Each HTTP request produces one `http.request` span with the request method,
path, and request id. When no OTLP endpoint is configured, spans are created
but not exported. Install the extra with `pip install "searchrelay[tracing]"`.

## Shared rate limiting and billing (Redis)

A single replica keeps rate limits in memory and balances in SQLite. To run
several replicas, point them at the same Redis:

```bash
SR_REDIS_URL=redis://redis:6379/0
```

With `SR_REDIS_URL` set:

- the account and per-domain rate limiters use an atomic Redis sliding window,
  so the limit applies across replicas;
- account balances and usage counters move to Redis, and the charge is a single
  atomic Lua script, so two replicas cannot oversell the same account;
- the SQLite ledger is no longer authoritative for balances.

Install the extra with `pip install "searchrelay[redis]"`. If Redis is
unreachable at startup the service fails fast with a clear error instead of
silently degrading to per-replica state.

The SQLite billing database is still used when `SR_REDIS_URL` is unset.

## Admin CLI

```bash
.venv/bin/searchrelay-admin --help
```

| Command | Purpose |
|---|---|
| `create-key --account demo --balance 100000000` | Create an API key and write it to the keys file |
| `set-budget --account demo --balance 50000000` | Change an account balance (add `--redis-url` to update Redis) |
| `usage [--account demo]` | Show billable usage per action |
| `list-keys` | List accounts, masked keys, and balances |

Balances are integer micro-US dollars (1 USD = 1,000,000 units). The CLI reads
`SR_KEYS_FILE`, `SR_DATABASE_PATH`, and `SR_REDIS_URL` from the environment, so
it operates on the same files the service loads. The service reads the keys
file at startup, so restart it (or reload the file) after `create-key`.

Example:

```console
$ searchrelay-admin create-key --account demo --balance 100000000 --keys-file data/keys.json
api_key=sk-2f9c1d0e5b...
account=demo
balance_micros=100000000
keys_file=data/keys.json
Restart the service (or reload its key file) to activate the key.
```

## Containers

The image runs as a non-root user (`appuser`, UID 10001) and ships a health
check that calls `/healthz`:

```bash
docker build -t searchrelay:local .
docker run --rm -p 8000:8000 \
  -e SR_API_KEYS_JSON='{"sk-test":"demo"}' \
  -e SR_INITIAL_BALANCES_JSON='{"demo":100000000}' \
  -e SR_UPSTREAM_URL=http://host.docker.internal:8080 \
  searchrelay:local
```

`docker-compose.yml` starts SearXNG, Redis, and the API:

```bash
export SEARXNG_SECRET="$(openssl rand -hex 32)"
export SR_API_KEYS_JSON='{"sk-test":"demo"}'
export SR_INITIAL_BALANCES_JSON='{"demo":100000000}'
docker compose up --build
```

The API waits for SearXNG and Redis to become healthy. `/healthz` and
`/readyz` return HTTP 503 when the search backend cannot be reached.

> The container image was **not** built or run in the environment where this
> documentation was written, because Docker was not available there. The
> Dockerfile and Compose file are provided as-is; verify them with
> `docker compose config` and `docker build` in your own environment.

## Health endpoints

| Endpoint | Meaning |
|---|---|
| `GET /healthz` | The service is running and the search backend answered |
| `GET /readyz` | The service is ready to accept traffic (same backend check) |
| `GET /metrics` | Prometheus exposition payload |
