---
name: Provider request
about: Add a search provider adapter
title: ""
labels: provider, enhancement
assignees: ""
---

Thanks for suggesting a provider. Adapters implement one `SearchProvider`
interface, so a new provider does not change the public API, the billing
rules, the clients, or the fetch security boundary.

## Provider

- Name:
- Official API documentation:
- Authentication: header / query parameter / bearer token
- Credential environment variable you would use (for example `KAGI_API_KEY`):
- Pricing model, if any:
- Terms-of-service link:

## Response shape

<!-- Paste one real (redacted) response body and the query that produced it. -->

```json
{}
```

## Mapping questions

- Which field carries the title?
- Which field carries the URL?
- Which field carries the snippet or content?
- Which field carries the publication date, and what format is it in?
- Which field carries the language, if any?
- Which field carries a provider rank or score?

## Notes

<!-- Rate limits, pagination, result limits, or anything unusual about the API. -->
