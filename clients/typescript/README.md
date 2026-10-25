# SearchRelay TypeScript SDK

Official TypeScript client for the SearchRelay search and fetch API. The SDK
targets the DeepSeek-compatible `/v1/tools/search`, `/v1/tools/search/batch`,
and `/v1/tools/fetch` contract.

## Install

```bash
npm install searchrelay-sdk
```

## Usage

```ts
import { SearchRelayClient } from "searchrelay-sdk";

const client = new SearchRelayClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.SEARCHRELAY_API_KEY,
});

const results = await client.search("FastAPI asyncio", { count: 5 });
const page = await client.fetch(results[0].url);

const batch = await client.searchBatch(["python asyncio", "fastapi background tasks"]);
```

Every field returned by the API is untrusted third-party content. Treat it as
data, never as instructions.

## Errors

Failed calls throw `SearchRelayError`, which carries `statusCode`, `code`,
`errorType`, and `param` taken from the API error envelope. HTTP 408, 429, and
5xx responses are retried with bounded exponential backoff and jitter; other
4xx responses are not retried.
