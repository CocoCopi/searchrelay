/**
 * SearchRelay TypeScript SDK.
 *
 * The client targets the DeepSeek-compatible `/v1/tools/search` and
 * `/v1/tools/fetch` contract. Every returned field is untrusted third-party
 * content and must be treated as data, never as instructions.
 */

export interface SearchOptions {
  count?: number;
  oriQuestion?: string;
  localization?: string;
  needContent?: boolean;
}

export interface SearchResult {
  rerank_score: number;
  title: string;
  url: string;
  snippet: string;
  summary: string;
  content: string;
  site_name: string | null;
  site_icon: string | null;
  date: string | null;
  authority: number;
}

export interface FetchPage {
  title: string;
  content: string;
}

export interface SearchBatchItem {
  query: string;
  results: SearchResult[];
  error: SearchRelayErrorBody | null;
}

export interface SearchBatchResponse {
  items: SearchBatchItem[];
}

export interface SearchRelayErrorBody {
  message: string;
  type: string;
  param: string | null;
  code: string;
}

export class SearchRelayError extends Error {
  readonly statusCode: number;
  readonly code: string;
  readonly errorType: string;
  readonly param: string | null;

  constructor(
    message: string,
    statusCode: number,
    code = "http_error",
    errorType = "invalid_request_error",
    param: string | null = null,
  ) {
    super(message);
    this.name = "SearchRelayError";
    this.statusCode = statusCode;
    this.code = code;
    this.errorType = errorType;
    this.param = param;
  }
}

export type FetchLike = (
  input: string,
  init?: {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
    signal?: AbortSignal;
  },
) => Promise<{
  status: number;
  ok: boolean;
  headers: { get(name: string): string | null };
  text(): Promise<string>;
}>;

export interface SearchRelayClientOptions {
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
  maxRetries?: number;
  fetchImpl?: FetchLike;
}

const RETRYABLE_STATUS = new Set([408, 429]);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryDelayMs(attempt: number, retryAfter: string | null): number {
  let delay = Math.min(8000, 500 * 2 ** attempt);
  if (retryAfter) {
    const seconds = Number(retryAfter);
    if (Number.isFinite(seconds)) {
      delay = Math.max(delay, seconds * 1000);
    } else {
      const parsed = Date.parse(retryAfter);
      if (!Number.isNaN(parsed)) {
        delay = Math.max(delay, parsed - Date.now());
      }
    }
  }
  return Math.min(delay, 30000) + Math.random() * 100;
}

export class SearchRelayClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly fetchImpl: FetchLike;

  constructor(options: SearchRelayClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "http://localhost:8000").replace(/\/+$/, "");
    this.apiKey = options.apiKey ?? "";
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.maxRetries = Math.max(0, options.maxRetries ?? 3);
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
  }

  async search(query: string, options: SearchOptions = {}): Promise<SearchResult[]> {
    const payload: Record<string, unknown> = {
      query,
      count: options.count ?? 10,
      localization: options.localization ?? "",
      need_content: options.needContent ?? false,
    };
    if (options.oriQuestion !== undefined) {
      payload.ori_question = options.oriQuestion;
    }
    const data = await this.post("/v1/tools/search", payload);
    const results = data.results;
    if (!Array.isArray(results)) {
      throw new SearchRelayError(
        "The API returned an invalid search response",
        200,
        "invalid_response",
      );
    }
    return results as SearchResult[];
  }

  async searchBatch(
    queries: string[],
    options: SearchOptions = {},
  ): Promise<SearchBatchResponse> {
    if (queries.length < 1 || queries.length > 10) {
      throw new SearchRelayError(
        "queries must contain between 1 and 10 items",
        0,
        "invalid_request",
      );
    }
    const payload: Record<string, unknown> = {
      queries,
      count: options.count ?? 10,
      localization: options.localization ?? "",
      need_content: options.needContent ?? false,
    };
    if (options.oriQuestion !== undefined) {
      payload.ori_question = options.oriQuestion;
    }
    const data = await this.post("/v1/tools/search/batch", payload);
    const items = data.items;
    if (!Array.isArray(items)) {
      throw new SearchRelayError(
        "The API returned an invalid batch search response",
        200,
        "invalid_response",
      );
    }
    return { items: items as SearchBatchItem[] };
  }

  async fetch(url: string): Promise<FetchPage> {
    const data = await this.post("/v1/tools/fetch", { url });
    return {
      title: typeof data.title === "string" ? data.title : "",
      content: typeof data.content === "string" ? data.content : "",
    };
  }

  private async post(path: string, payload: unknown): Promise<Record<string, unknown>> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    if (this.apiKey) {
      headers.Authorization = `Bearer ${this.apiKey}`;
    }
    let lastTransportError: unknown = null;

    for (let attempt = 0; attempt <= this.maxRetries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        if (
          (RETRYABLE_STATUS.has(response.status) || response.status >= 500) &&
          attempt < this.maxRetries
        ) {
          await sleep(retryDelayMs(attempt, response.headers.get("retry-after")));
          continue;
        }
        const text = await response.text();
        if (!response.ok) {
          throw await buildError(response.status, text);
        }
        try {
          const parsed = JSON.parse(text) as unknown;
          if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("not an object");
          }
          return parsed as Record<string, unknown>;
        } catch {
          throw new SearchRelayError(
            "The API returned invalid JSON",
            response.status,
            "invalid_response",
          );
        }
      } catch (error) {
        if (error instanceof SearchRelayError) {
          throw error;
        }
        lastTransportError = error;
        if (attempt >= this.maxRetries) {
          break;
        }
        await sleep(retryDelayMs(attempt, null));
      } finally {
        clearTimeout(timer);
      }
    }

    throw new SearchRelayError(
      `SearchRelay request failed: ${String(lastTransportError ?? "unknown error")}`,
      0,
      "transport_error",
    );
  }
}

async function buildError(status: number, text: string): Promise<SearchRelayError> {
  let message = `Search/Fetch request failed with HTTP ${status}`;
  let code = "http_error";
  let errorType = "invalid_request_error";
  let param: string | null = null;
  try {
    const parsed = JSON.parse(text) as { error?: Record<string, unknown> };
    const error = parsed?.error;
    if (error && typeof error === "object") {
      if (typeof error.message === "string") message = error.message;
      if (typeof error.code === "string") code = error.code;
      if (typeof error.type === "string") errorType = error.type;
      if (typeof error.param === "string" || error.param === null) {
        param = error.param;
      }
    }
  } catch {
    // Keep the default message when the body is not JSON.
  }
  return new SearchRelayError(message, status, code, errorType, param);
}

export default SearchRelayClient;
