/**
 * Typed error hierarchy raised by the AMI SDK.
 *
 * Every HTTP error returned by the AMI backend is mapped to a specific
 * subclass so callers can `catch` the case they care about without parsing
 * status codes.
 *
 * Mapping:
 *
 *   400 -> AmiValidationError
 *   401 -> AmiAuthError
 *   403 -> AmiAuthError
 *   404 -> AmiNotFoundError
 *   409 -> AmiConflictError
 *   429 -> AmiRateLimitError
 *   5xx -> AmiServerError
 *   transport / DNS / timeout / abort -> AmiTransportError
 */

export interface AmiErrorOptions {
  statusCode?: number;
  detail?: Record<string, unknown> | null;
  reason?: string | null;
  cause?: unknown;
}

/**
 * Base class for every error the SDK raises. All HTTP errors expose:
 *  - `statusCode`: the HTTP status returned by the backend.
 *  - `detail`: the parsed JSON body (or `null` if non-JSON).
 *  - `reason`: the machine-readable `error` field that AMI puts in every
 *    error body (e.g. `"sms_hourly_limit_exceeded"`).
 */
export class AmiError extends Error {
  readonly statusCode: number | undefined;
  readonly detail: Record<string, unknown> | null | undefined;
  readonly reason: string | null | undefined;

  constructor(message: string, options: AmiErrorOptions = {}) {
    super(message);
    this.name = this.constructor.name;
    this.statusCode = options.statusCode;
    this.detail = options.detail ?? null;
    this.reason = options.reason ?? null;
    if (options.cause !== undefined) {
      (this as { cause?: unknown }).cause = options.cause;
    }
  }
}

/** Raised on 401 / 403. The API key or agent_token is missing or invalid. */
export class AmiAuthError extends AmiError {}

/** Raised on 404. The referenced resource does not exist. */
export class AmiNotFoundError extends AmiError {}

/**
 * Raised on 409. The resource is in a state that does not allow the
 * requested transition (e.g. activating a MobileIdentity from an unsigned
 * contract).
 */
export class AmiConflictError extends AmiError {}

/** Raised on 400. The request body or query failed validation. */
export class AmiValidationError extends AmiError {}

/**
 * Raised on 429. A policy limit was hit (per-hour, per-day, monthly budget,
 * or blocked country prefix). `reason` carries the specific code such as
 * `sms_hourly_limit_exceeded`, `monthly_budget_exceeded`, `country_not_allowed`.
 */
export class AmiRateLimitError extends AmiError {
  /** Convenience: same as `reason`. The specific policy that fired. */
  get policy(): string | null | undefined {
    return this.reason;
  }
}

/**
 * Raised on 5xx. The backend failed; retrying after a short delay is usually
 * appropriate. The SDK already performs limited retries on idempotent GETs.
 */
export class AmiServerError extends AmiError {
  /** Suggested seconds to wait before retrying. Filled by the SDK. */
  readonly retryAfterSec: number | undefined;

  constructor(message: string, options: AmiErrorOptions & { retryAfterSec?: number } = {}) {
    super(message, options);
    this.retryAfterSec = options.retryAfterSec;
  }
}

/**
 * Raised when the HTTP call never produced a response (timeout, DNS failure,
 * refused connection, TLS error, or the request was aborted by the caller).
 */
export class AmiTransportError extends AmiError {}

/**
 * Maps an HTTP status + parsed body into the right subclass. Used by the
 * internal HTTP wrapper; exposed for advanced users who want to map their
 * own responses.
 */
export function errorFromResponse(
  status: number,
  body: Record<string, unknown> | null,
  fallbackMessage?: string,
): AmiError {
  const reason = typeof body?.error === "string" ? body.error : null;
  const detail = body && typeof body === "object" ? body : null;
  const message =
    fallbackMessage ??
    (reason ? `AMI request failed: ${reason}` : `AMI request failed with status ${status}`);

  const opts: AmiErrorOptions = { statusCode: status, detail, reason };

  if (status === 400) return new AmiValidationError(message, opts);
  if (status === 401 || status === 403) return new AmiAuthError(message, opts);
  if (status === 404) return new AmiNotFoundError(message, opts);
  if (status === 409) return new AmiConflictError(message, opts);
  if (status === 429) return new AmiRateLimitError(message, opts);
  if (status >= 500) return new AmiServerError(message, opts);

  return new AmiError(message, opts);
}
