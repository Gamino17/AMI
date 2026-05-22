/**
 * Tiny in-memory `fetch` mock used by every test file. Each test registers
 * route handlers, then constructs an `AmiClient` / `AmiAgent` with the mock
 * as the `fetch` implementation. The mock asserts standard headers
 * (Authorization, Content-Type), records every request and lets the test
 * shape responses freely.
 */

export interface RecordedRequest {
  method: string;
  url: string;
  pathname: string;
  query: Record<string, string>;
  headers: Record<string, string>;
  body: unknown;
}

export type RouteHandler = (req: RecordedRequest) => Response | Promise<Response>;

export interface MockFetch {
  fetch: typeof fetch;
  on(method: string, path: string, handler: RouteHandler): void;
  requests: RecordedRequest[];
  reset(): void;
}

export function createMockFetch(): MockFetch {
  const routes = new Map<string, RouteHandler>();
  const requests: RecordedRequest[] = [];

  const key = (method: string, path: string) => `${method.toUpperCase()} ${path}`;

  const fetchImpl = (async (
    input: string | URL | Request,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : input.toString();
    const u = new URL(url);
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    const rawHeaders = init?.headers;
    if (rawHeaders) {
      if (rawHeaders instanceof Headers) {
        rawHeaders.forEach((v, k) => {
          headers[k.toLowerCase()] = v;
        });
      } else if (Array.isArray(rawHeaders)) {
        for (const [k, v] of rawHeaders) headers[k.toLowerCase()] = v;
      } else {
        for (const [k, v] of Object.entries(rawHeaders)) {
          headers[k.toLowerCase()] = String(v);
        }
      }
    }
    let body: unknown = undefined;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    } else if (init?.body != null) {
      body = init.body;
    }
    const query: Record<string, string> = {};
    u.searchParams.forEach((v, k) => {
      query[k] = v;
    });
    const rec: RecordedRequest = {
      method,
      url,
      pathname: u.pathname,
      query,
      headers,
      body,
    };
    requests.push(rec);

    const handler =
      routes.get(key(method, u.pathname)) ??
      routes.get(key(method, "*"));
    if (!handler) {
      return new Response(
        JSON.stringify({ error: "no_route_registered", path: u.pathname, method }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      );
    }
    // Race against the optional AbortSignal so callers can test cancellation.
    const signal = init?.signal;
    const handlerPromise = Promise.resolve().then(() => handler(rec));
    if (!signal) return handlerPromise;
    if (signal.aborted) {
      throw new DOMException(
        signal.reason instanceof Error ? signal.reason.message : "aborted",
        "AbortError",
      );
    }
    return new Promise<Response>((resolve, reject) => {
      const onAbort = () => {
        const reason = (signal as AbortSignal).reason;
        reject(
          reason instanceof Error
            ? reason
            : new DOMException(String(reason ?? "aborted"), "AbortError"),
        );
      };
      signal.addEventListener("abort", onAbort, { once: true });
      handlerPromise.then(
        (res) => {
          signal.removeEventListener("abort", onAbort);
          resolve(res);
        },
        (err) => {
          signal.removeEventListener("abort", onAbort);
          reject(err);
        },
      );
    });
  }) as unknown as typeof fetch;

  return {
    fetch: fetchImpl,
    on(method, path, handler) {
      routes.set(key(method, path), handler);
    },
    requests,
    reset() {
      routes.clear();
      requests.length = 0;
    },
  };
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
