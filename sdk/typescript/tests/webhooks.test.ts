import { describe, it, expect } from "vitest";
import { createHmac } from "node:crypto";
import { verifySignature, signPayload } from "../src/webhooks.js";

const SECRET = "whsec_" + "a".repeat(64);
const BODY = JSON.stringify({
  event: "sms.inbound",
  delivered_at: "2026-05-22T00:00:00Z",
  mid: "mid_1",
  data: { id: "msg_1" },
});

function sigFor(body: string | Uint8Array): string {
  const h = createHmac("sha256", SECRET);
  h.update(body);
  return "sha256=" + h.digest("hex");
}

describe("verifySignature", () => {
  it("accepts a correct signature on a string body", () => {
    expect(
      verifySignature({
        secret: SECRET,
        body: BODY,
        signatureHeader: sigFor(BODY),
      }),
    ).toBe(true);
  });

  it("accepts a correct signature on a Buffer body", () => {
    const buf = Buffer.from(BODY, "utf-8");
    expect(
      verifySignature({
        secret: SECRET,
        body: buf,
        signatureHeader: sigFor(buf),
      }),
    ).toBe(true);
  });

  it("accepts the header as a single-element array (Node IncomingHttpHeaders)", () => {
    expect(
      verifySignature({
        secret: SECRET,
        body: BODY,
        signatureHeader: [sigFor(BODY)],
      }),
    ).toBe(true);
  });

  it("rejects a tampered body", () => {
    const tampered = BODY + "!";
    expect(
      verifySignature({
        secret: SECRET,
        body: tampered,
        signatureHeader: sigFor(BODY),
      }),
    ).toBe(false);
  });

  it("rejects a wrong secret", () => {
    expect(
      verifySignature({
        secret: "whsec_other",
        body: BODY,
        signatureHeader: sigFor(BODY),
      }),
    ).toBe(false);
  });

  it("rejects a missing or malformed header", () => {
    expect(verifySignature({ secret: SECRET, body: BODY, signatureHeader: undefined })).toBe(false);
    expect(verifySignature({ secret: SECRET, body: BODY, signatureHeader: null })).toBe(false);
    expect(verifySignature({ secret: SECRET, body: BODY, signatureHeader: "" })).toBe(false);
    expect(verifySignature({ secret: SECRET, body: BODY, signatureHeader: "sha1=abc" })).toBe(false);
    expect(verifySignature({ secret: SECRET, body: BODY, signatureHeader: "sha256=zz" })).toBe(false);
    expect(
      verifySignature({ secret: SECRET, body: BODY, signatureHeader: "sha256=" + "z".repeat(64) }),
    ).toBe(false);
  });

  it("signPayload returns a stable hex digest", () => {
    const a = signPayload(SECRET, BODY);
    const b = signPayload(SECRET, BODY);
    expect(a).toBe(b);
    expect(a).toHaveLength(64);
    expect(/^[0-9a-f]+$/.test(a)).toBe(true);
  });

  it("signPayload throws on empty secret", () => {
    expect(() => signPayload("", BODY)).toThrow();
  });
});
