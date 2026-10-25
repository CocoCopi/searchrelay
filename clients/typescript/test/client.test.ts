import assert from "node:assert/strict";
import { test } from "node:test";

import { SearchRelayClient, SearchRelayError } from "../src/index.js";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  const text = JSON.stringify(body);
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: {
      get(name: string) {
        const key = name.toLowerCase();
        for (const [header, value] of Object.entries(headers)) {
          if (header.toLowerCase() === key) return value;
        }
        return null;
      },
    },
    async text() {
      return text;
    },
  };
}

test("search posts the DeepSeek-compatible payload and returns results", async () => {
  const calls: Array<{ url: string; init?: { body?: string } }> = [];
  const client = new SearchRelayClient({
    baseUrl: "http://api.test/",
    apiKey: "sk-test",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse(200, {
        results: [
          {
            rerank_score: 1.5,
            title: "Example",
            url: "https://example.com",
            snippet: "snippet",
            summary: "summary",
            content: "",
            site_name: "example.com",
            site_icon: "https://example.com/favicon.ico",
            date: null,
            authority: 2,
          },
        ],
      });
    },
  });

  const results = await client.search("fastapi", { count: 3, needContent: true });
  assert.equal(results.length, 1);
  assert.equal(results[0].url, "https://example.com");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://api.test/v1/tools/search");
  const payload = JSON.parse(calls[0].init?.body ?? "{}");
  assert.deepEqual(payload, {
    query: "fastapi",
    count: 3,
    localization: "",
    need_content: true,
  });
});

test("fetch returns title and content", async () => {
  const client = new SearchRelayClient({
    fetchImpl: async () => jsonResponse(200, { title: "T", content: "# T" }),
  });
  const page = await client.fetch("https://example.com");
  assert.equal(page.title, "T");
  assert.equal(page.content, "# T");
});

test("searchBatch rejects more than ten queries", async () => {
  const client = new SearchRelayClient({ fetchImpl: async () => jsonResponse(200, {}) });
  await assert.rejects(
    () => client.searchBatch(Array.from({ length: 11 }, (_, index) => `q${index}`)),
    (error: unknown) => error instanceof SearchRelayError,
  );
});

test("server errors are surfaced with the API error envelope", async () => {
  const client = new SearchRelayClient({
    maxRetries: 0,
    fetchImpl: async () =>
      jsonResponse(402, {
        error: {
          message: "Account balance is insufficient",
          type: "invalid_request_error",
          param: null,
          code: "insufficient_balance",
        },
      }),
  });
  await assert.rejects(
    () => client.search("anything"),
    (error: unknown) => {
      assert.ok(error instanceof SearchRelayError);
      assert.equal(error.statusCode, 402);
      assert.equal(error.code, "insufficient_balance");
      assert.equal(error.message, "Account balance is insufficient");
      return true;
    },
  );
});

test("retryable statuses are retried up to maxRetries", async () => {
  let attempts = 0;
  const client = new SearchRelayClient({
    maxRetries: 2,
    fetchImpl: async () => {
      attempts += 1;
      if (attempts < 3) return jsonResponse(503, { error: { message: "busy" } });
      return jsonResponse(200, { results: [] });
    },
  });
  const results = await client.search("anything");
  assert.deepEqual(results, []);
  assert.equal(attempts, 3);
});
