# Security model

SearchRelay fetches URLs that an agent chooses, which makes server-side
request forgery (SSRF) the primary threat. The fetch path is deliberately
stricter than an ordinary HTTP client. The second concern is prompt
injection: retrieved text is attacker-controlled data, not instructions.

## Threat model

| Threat | Mitigation |
|---|---|
| Fetch of `169.254.169.254` or another metadata endpoint | Public-IP-only resolution, re-checked on every redirect |
| Fetch of an internal service (`10.0.0.5`, `192.168.x.x`, `172.16.x.x`) | Same public-IP rule |
| DNS rebinding between validation and connection | The connection is pinned to the validated address |
| Redirect from a public host to a private host | Every redirect is revalidated before it is followed |
| Proxy variables smuggling requests to internal hosts | `httpx` is created with `trust_env=False` |
| Non-HTTP schemes (`file:`, `gopher:`, `dict:`) | Scheme allowlist: HTTP and HTTPS only |
| URL with embedded credentials (`http://user:pass@host/`) | Rejected during validation |
| Unusual ports | Only 80 and 443 are accepted |
| Oversized or slow responses | Byte limit, read timeout, and request body limit |
| Content that tries to steer the model | Instruction filtering, untrusted labeling, and an agent boundary in the examples |
| One account spending another's balance | Per-account ledger with atomic updates, shared across replicas through Redis |

## The fetch policy

Applied in order, for the initial request and again for every redirect:

1. **Parse and normalize.** Reject empty URLs, URLs longer than 8192
   characters, control characters, and backslashes.
2. **Scheme and structure.** Only `http` and `https`; a host is required; user
   information is forbidden; the effective port must be 80 or 443.
3. **Host policy.** The hostname is normalized (IDNA, lowercase, trailing dot
   removed). Blocked hostnames include `localhost`, `metadata`,
   `metadata.google.internal`, `instance-data.ec2.internal`, and any hostname
   ending in `.local`, `.localhost`, `.internal`, `.home`, `.lan`, or
   `.home.arpa`. Single-label hostnames are rejected.
4. **Operator policy.** `SR_BLOCKED_DOMAINS_CSV` blocks hosts, and
   `SR_ALLOWED_DOMAINS_CSV` restricts fetching to an allowlist when set. Both
   match subdomains.
5. **Address policy.** Every A and AAAA record is resolved. If **any** resolved
   address is not globally routable, the request is refused with `403`.
   Loopback, private, link-local, multicast, reserved, CGNAT, and unspecified
   addresses are all refused. IPv4-mapped IPv6 addresses are unwrapped before
   the check.
6. **Connection pinning.** The request is sent to the validated address while
   the original `Host` header and TLS SNI hostname are preserved, so the
   certificate still has to match the hostname the caller asked for.
7. **Redirects.** The redirect budget is `SR_FETCH_MAX_REDIRECTS` (default 5),
   and each hop repeats steps 1–6.
8. **Content policy.** `robots.txt` is honored by default
   (`SR_RESPECT_ROBOTS`), the content type and body size are limited
   (`SR_FETCH_MAX_BYTES`, default 5 MiB), and the read timeout is
   `SR_FETCH_TIMEOUT_SECONDS` (default 12s).

The crawler used by the optional local index applies the same policy to the
pages it indexes, so a seed domain cannot be used to reach internal services.

## Prompt injection

Retrieved pages are data, never instructions. SearchRelay does three things
about that, and they are not sufficient on their own:

1. **Filtering.** Common prompt-override instructions ("ignore previous
   instructions", "disregard the system prompt", and similar) are removed from
   the extracted content, along with scripts, styles, comments, hidden
   elements, forms, and embedded objects.
2. **Labeling.** Every content field in the OpenAPI document is described as
   untrusted third-party content, and every response carries
   `X-Content-Untrusted: true`.
3. **Agent boundary.** The example agent isolates retrieved documents inside
   explicit delimiters and instructs the answering model never to follow
   instructions found in retrieved content.

!!! warning "No filter is a guarantee"
    No semantic prompt-injection detector can prove a document is safe. The
    filter raises the cost of trivial attacks; the agent boundary and the model
    instruction are the actual defense. Keep retrieved content out of your
    system prompt.

## Authentication, limits, and billing

- Bearer keys are compared against the configured key map; there is no default
  key and no fallback identity.
- Search and Fetch have independent per-account sliding-window limits.
- Fetch additionally has a global per-domain limit, so one account cannot
  hammer a single site.
- Balances are integer micro-US dollars, and a request is billed only when it
  succeeds by the published rule. With `SR_REDIS_URL` set, the ledger and the
  limit counters are shared across replicas so several instances cannot each
  spend the same budget.
- The service refuses to start when no API key is configured, when the
  authority file is malformed, or when the optional reranker is enabled without
  a usable model.

## Secrets

- No credential is committed to the repository, and the deployment files carry
  placeholders that fail loudly if they are left unset.
- `docker-compose.yml` requires `SEARXNG_SECRET`, `SR_API_KEYS_JSON`, and
  `SR_INITIAL_BALANCES_JSON` in the environment and refuses to start without
  them.
- Keep `.env` out of version control, use a secret manager in production, and
  rotate keys before exposing a deployment publicly.

## Reporting a vulnerability

Please report security issues privately as described in `SECURITY.md` in the
repository root rather than opening a public issue. Include a description, the
affected version or commit, and a reproduction if you have one. You can expect
an acknowledgement and an assessment of the report.
