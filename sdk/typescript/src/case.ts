/**
 * snake_case <-> camelCase conversion helpers.
 *
 * The AMI HTTP API uses snake_case JSON on the wire (`mobile_identity_id`,
 * `agent_token`, ...). TypeScript callers expect camelCase. These helpers
 * transform plain JSON objects/arrays recursively without touching non-plain
 * values (Date, Buffer, class instances, primitives).
 */

const SNAKE_TO_CAMEL_RE = /_([a-z0-9])/g;
const CAMEL_BOUNDARY_RE = /([a-z0-9])([A-Z])/g;

/** Converts `mobile_identity_id` -> `mobileIdentityId`. */
export function snakeToCamel(key: string): string {
  return key.replace(SNAKE_TO_CAMEL_RE, (_, c) => c.toUpperCase());
}

/** Converts `mobileIdentityId` -> `mobile_identity_id`. */
export function camelToSnake(key: string): string {
  return key.replace(CAMEL_BOUNDARY_RE, "$1_$2").toLowerCase();
}

/**
 * Recursively rewrites every object key in `value` from snake_case to
 * camelCase. Arrays are mapped element-wise. Non-plain values pass through.
 */
export function toCamel<T = unknown>(value: unknown): T {
  return transform(value, snakeToCamel) as T;
}

/**
 * Recursively rewrites every object key in `value` from camelCase to
 * snake_case. Arrays are mapped element-wise. Non-plain values pass through.
 */
export function toSnake<T = unknown>(value: unknown): T {
  return transform(value, camelToSnake) as T;
}

function transform(value: unknown, fn: (k: string) => string): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => transform(item, fn));
  }
  if (isPlainObject(value)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      out[fn(k)] = transform(v, fn);
    }
    return out;
  }
  return value;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
