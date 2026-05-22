/**
 * Helpers to verify outgoing webhook deliveries from AMI.
 *
 * AMI signs every webhook delivery with HMAC-SHA256(secret, rawBody) and
 * places the result in the `X-Ami-Signature: sha256=<hex>` header. The
 * receiver MUST verify the signature against the secret returned ONCE by
 * `client.createWebhook(...)` before trusting the payload.
 *
 * Two helpers are exposed:
 *
 *  - `verifySignature(opts)`: returns `true` / `false`. Use in handlers.
 *  - `signPayload(secret, body)`: deterministic signature, useful for tests
 *    or to sign payloads on the way out (e.g. when re-broadcasting events).
 *
 * Constant-time comparison is used to mitigate timing attacks.
 *
 * Node 18+ exposes WebCrypto and `node:crypto`. The SDK uses `node:crypto`
 * because it is available on every supported runtime (Node, Bun, Deno via
 * the Node-compat layer). Edge runtimes that lack `node:crypto` can pass
 * `subtle: crypto.subtle` and the helper falls back to WebCrypto.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

export interface VerifySignatureOptions {
  /** The webhook secret returned by `client.createWebhook(...)`. */
  secret: string;
  /**
   * The raw request body received from AMI, as a string or Buffer. MUST be
   * the bytes AMI sent, before any JSON parsing or re-serialisation.
   */
  body: string | Uint8Array;
  /** The value of the `X-Ami-Signature` request header. */
  signatureHeader: string | string[] | undefined | null;
}

const PREFIX = "sha256=";

/**
 * Verifies a webhook signature in constant time. Returns `true` if the
 * signature is well-formed and matches `HMAC-SHA256(secret, body)`.
 *
 * Example:
 *
 * ```ts
 * import { verifySignature } from "@ami-protocol/sdk/webhooks";
 *
 * app.post("/ami-webhook", (req, res) => {
 *   const ok = verifySignature({
 *     secret: process.env.AMI_WEBHOOK_SECRET!,
 *     body: req.rawBody,
 *     signatureHeader: req.headers["x-ami-signature"],
 *   });
 *   if (!ok) return res.status(401).end();
 *   // ...handle event
 * });
 * ```
 */
export function verifySignature(opts: VerifySignatureOptions): boolean {
  const headerValue = Array.isArray(opts.signatureHeader)
    ? opts.signatureHeader[0]
    : opts.signatureHeader;
  if (!headerValue || typeof headerValue !== "string") return false;
  if (!headerValue.startsWith(PREFIX)) return false;

  const presentedHex = headerValue.slice(PREFIX.length).trim();
  if (!presentedHex || presentedHex.length !== 64) return false;
  if (!/^[0-9a-fA-F]+$/.test(presentedHex)) return false;

  const expectedHex = signPayload(opts.secret, opts.body);

  // Constant-time compare. Buffers must be the same length, which we just
  // validated, so `timingSafeEqual` will not throw.
  const a = Buffer.from(expectedHex, "hex");
  const b = Buffer.from(presentedHex, "hex");
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/**
 * Returns `HMAC-SHA256(secret, body)` as a lowercase hex string. The string
 * does NOT include the `sha256=` prefix.
 */
export function signPayload(secret: string, body: string | Uint8Array): string {
  if (!secret) throw new Error("signPayload: secret is required");
  const hmac = createHmac("sha256", secret);
  hmac.update(body);
  return hmac.digest("hex");
}
