/**
 * Minimal fetch wrapper used by the SDK.
 *
 * Responsibilities:
 *  - Inject Authorization header (Bearer customer API key or agent token).
 *  - Serialize request bodies as JSON with snake_case keys.
 *  - Parse responses and convert snake_case keys back to camelCase.
 *  - Map non-2xx responses to the right `AmiError` subclass.
 *  - Retry GETs and 5xx responses with a small exponential backoff.
 *  - Honor an optional `AbortSignal` from the caller.
 *
 * No runtime dependencies — relies on the global `fetch` (Node 18+, every
 * modern browser, every JS edge runtime).
 */

import {
  AmiError,
  AmiServerError,
  AmiTransportError,
  errorFromResponse,
} from "./errors.js";
import { toCamel, toSnake } from "./case.js";

/** Subset of `fetch` we use; lets users inject their own implementation. */
export type FetchLike = (
  input: string | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface HttpOptions {
  baseUrl: string;
  /** Either `apiKey` (Level 1) or `agentToken` (Level 2). */
  authToken: string;
  /** Defaults to the global `fetch`. */
  fetch?: FetchLike;
  /** Optional user-agent suffix appended to "@ami-protocol/sdk-ts/<version>". */
  userAgent?: string;
  /** Per-request timeout in ms. Defaults to 30s. Set to 0 to disable. */
  timeoutMs?: number;
  /** Number of retries on transport errors and 5xx. Defaults to 2. */
  maxRetries?: number;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE" | "PUT";
  /** JSON-serialisable body. Camel-cased; will be converted to snake_case. */
  body?: unknown;
  /** Query string params. Camel-cased; will be converted to snake_case. */
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Caller-supplied abort signal. */
  signal?: AbortSignal;
  /** Override the default retry count for this call. */
  maxRetries?: number;
}

const SDK_VERSION = "0.1.0";
const SDK_USER_AGENT_BASE = `@ami-protocol/sdk-ts/${SDK_VERSION}`;
const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const RETRY_DELAYS_MS = [200, 600, 1400];

export class HttpClient {
  private readonly baseUrl: string;
  private readonly authToken: string;
  private readonly fetchImpl: FetchLike;
  private readonly userAgent: string;
  private readonly timeoutMs: number;
  private readonly defaultRetries: number;

  constructor(opts: HttpOptions) {
    if (!opts.baseUrl) throw new Error("baseUrl is required");
    if (!opts.authToken) throw new Error("authToken is required");
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.authToken = opts.authToken;
    this.fetchImpl =
      opts.fetch ??
      (typeof fetch !== "undefined"
        ? (fetch.bind(globalThis) as FetchLike)
        : (() => {
            throw new Error(
              "No global fetch available. Pass a `fetch` implementation in AmiClient options.",
            );
          }));
    this.userAgent = opts.userAgent
      ? `${SDK_USER_AGENT_BASE} ${opts.userAgent}`
      : SDK_USER_AGENT_BASE;
    this.timeoutMs = opts.timeoutMs ?? 30_000;
    this.defaultRetries = opts.maxRetries ?? 2;
  }

  async request<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
    const method = opts.method ?? "GET";
    const url = this.buildUrl(path, opts.query);
    const body =
      opts.body === undefined || opts.body === null
        ? undefined
        : JSON.stringify(toSnake(opts.body));

    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.authToken}`,
      Accept: "application/json",
      "User-Agent": this.userAgent,
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";

    const retries = opts.maxRetries ?? this.defaultRetries;
    const idempotent = method === "GET";

    let lastError: unknown;
    const attempts = retries + 1;
    for (let attempt = 0; attempt < attempts; attempt++) {
      const { signal, cleanup } = this.makeSignal(opts.signal);
      try {
        const response = await this.fetchImpl(url, {
          method,
          headers,
          body,
          signal,
        });
        cleanup();
        const text = await response.text();
        const parsed = this.parseJson(text);

        if (response.ok) {
          return toCamel(parsed) as T;
        }

        // Retry 5xx for both idempotent and explicitly retried calls.
        if (
          RETRYABLE_STATUSES.has(response.status) &&
          (idempotent || attempt === 0) &&
          attempt < attempts - 1
        ) {
          await this.delay(this.backoffMs(attempt));
          continue;
        }

        const errBody = parsed as Record<string, unknown> | null;
        const err = errorFromResponse(response.status, errBody);
        // Attach retry hint when relevant.
        if (err instanceof AmiServerError) {
          const retryAfter = response.headers.get("retry-after");
          if (retryAfter) {
            const parsedRetry = parseInt(retryAfter, 10);
            if (!Number.isNaN(parsedRetry)) {
              (err as { retryAfterSec?: number }).retryAfterSec = parsedRetry;
            }
          }
        }
        throw err;
      } catch (err) {
        cleanup();
        // Re-throw any HTTP error already mapped above (validation, auth,
        // conflict, rate-limit, not-found, server, plain AmiError).
        if (err instanceof AmiError) {
          throw err;
        }
        // Transport-level error: timeout, DNS, refused connection, abort.
        lastError = err;
        if (idempotent && attempt < attempts - 1) {
          await this.delay(this.backoffMs(attempt));
          continue;
        }
        throw this.wrapTransportError(err);
      }
    }
    // Loop drained without success (only via continue from retryable 5xx).
    throw this.wrapTransportError(lastError);
  }

  // -------------------------------------------------------------------------

  private buildUrl(path: string, query?: RequestOptions["query"]): string {
    const full = path.startsWith("http") ? path : this.baseUrl + path;
    if (!query) return full;
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      // Query keys go snake_case on the wire too.
      const snakeKey = k.replace(/[A-Z]/g, (m) => `_${m.toLowerCase()}`);
      params.set(snakeKey, String(v));
    }
    const qs = params.toString();
    if (!qs) return full;
    return full.includes("?") ? `${full}&${qs}` : `${full}?${qs}`;
  }

  private parseJson(text: string): unknown {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      return null;
    }
  }

  private wrapTransportError(err: unknown): AmiTransportError {
    if (err instanceof AmiTransportError) return err;
    const message =
      err instanceof Error ? err.message : `Transport failure: ${String(err)}`;
    return new AmiTransportError(message, { cause: err });
  }

  private makeSignal(externalSignal?: AbortSignal): {
    signal: AbortSignal | undefined;
    cleanup: () => void;
  } {
    if (!this.timeoutMs && !externalSignal) {
      return { signal: undefined, cleanup: () => undefined };
    }
    if (!this.timeoutMs && externalSignal) {
      return { signal: externalSignal, cleanup: () => undefined };
    }
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort((externalSignal as AbortSignal).reason);
    if (externalSignal) {
      if (externalSignal.aborted) {
        ctrl.abort(externalSignal.reason);
      } else {
        externalSignal.addEventListener("abort", onAbort, { once: true });
      }
    }
    const timer =
      this.timeoutMs > 0
        ? setTimeout(
            () => ctrl.abort(new Error(`Request timed out after ${this.timeoutMs}ms`)),
            this.timeoutMs,
          )
        : null;
    return {
      signal: ctrl.signal,
      cleanup: () => {
        if (timer) clearTimeout(timer);
        if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
      },
    };
  }

  private backoffMs(attempt: number): number {
    return RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)]!;
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
