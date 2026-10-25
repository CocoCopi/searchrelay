---
name: Bug report
about: Something behaves differently from what the documentation says
title: ""
labels: bug
assignees: ""
---

## What happened

<!-- A clear description of the wrong behavior. -->

## What you expected

<!-- What the documentation, the API contract, or your intuition says should happen. -->

## Reproduction

<!-- The exact steps. Include the request body and the command you ran. -->

```bash
curl -sS http://localhost:8000/v1/tools/search \
  -H "Authorization: Bearer $SEARCHRELAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"...","count":5}'
```

## Actual response

<!-- Paste the full response body, including the error envelope. -->

```json
{}
```

## Environment

- SearchRelay version or commit:
- Deployment: Docker Compose / pip install / other
- Python version:
- Provider(s) enabled (`SR_PROVIDER_PRIORITIES`):
- Operating system:

## Logs

<!-- Structured log lines from the request, if you have them. Please redact keys and secrets. -->

```text
```

## Additional context

<!-- Anything else: configuration, related issues, screenshots. -->
