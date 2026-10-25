# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| < 0.1 | No |

SearchRelay is pre-1.0. Security fixes are applied to the latest minor
release; there is no long-term-support branch yet.

## Reporting a vulnerability

**Please do not open a public issue, pull request, or discussion for a
security problem.** Public reports give an attacker a head start.

Report privately through GitHub's private vulnerability reporting on the
repository's Security tab ("Report a vulnerability"). If that is unavailable,
open a regular issue that contains only a one-line request for a private
contact channel, and wait for a reply before sharing any detail.

Please include:

- what the issue is and which component is affected (`app/fetch.py`,
  `app/security.py`, the admin CLI, a provider adapter, …);
- the version, tag, or commit you tested;
- the exact steps to reproduce, including any request payloads;
- the impact you believe it has, and any evidence you already collected;
- whether the issue is already public anywhere.

Please do not include third-party credentials, real user data, or anything
else you would not want published. A minimal reproduction against a local
instance is ideal.

## What to expect

- An acknowledgement within a few days.
- An assessment of the report, including whether it is accepted, and a rough
  timeline for a fix.
- A credit in the release notes and the changelog, if you want one. Say so in
  your report.

If a report is declined, you will get the reason. If you disagree, we are
happy to discuss it.

## Handling a confirmed issue

The usual path is: fix, add a regression test, release a patch version, and
publish an advisory describing the problem, the impact, and the fixed version.
We aim to keep the advisory public only after users have had a reasonable
window to upgrade.

## Security model in brief

The parts most worth attacking, and how they are defended:

| Area | Defense |
|---|---|
| SSRF through the fetch endpoint | Public-IP-only DNS resolution, DNS pinning, redirect revalidation, scheme/port/userinfo restrictions, no ambient proxy trust |
| Cloud metadata access | The metadata address ranges are not globally routable and are refused by the same rule |
| Prompt injection through fetched pages | Instruction filtering, untrusted labeling of every field, `X-Content-Untrusted: true`, and the agent boundary in the examples |
| Billing abuse | Atomic balance updates, per-account limits, and a shared ledger through Redis for multi-replica deployments |
| Credential leakage | No default keys or secrets, no credentials in the repository, and startup failures that name the missing setting without printing its value |
| Unbounded resource use | Request body, response size, timeout, redirect, and per-provider budget limits |

The full model is documented in [`docs/security.md`](docs/security.md).

## Out of scope

- Vulnerabilities in upstream providers (Brave, Serper, Tavily, Exa, SearXNG,
  or the engines a SearXNG instance queries). Report those to the projects
  that own them.
- The fact that the service fetches public URLs on request. That is the
  product.
- Denial of service by an authenticated account deliberately exceeding its own
  documented limits.
- Findings from automated scanners without a demonstrated impact.
