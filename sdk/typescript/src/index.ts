/**
 * @ami-protocol/sdk — official TypeScript SDK for the AMI protocol.
 *
 * Two entry points:
 *
 *  - `AmiClient`: authenticates with the customer's API key. Covers the
 *    contracting flow, MobileIdentity administration, limits, webhooks.
 *  - `AmiAgent`: authenticates with the scoped `agent_token`. Covers SMS,
 *    voice and usage for a single MobileIdentity.
 *
 * Webhook verification helpers live under `@ami-protocol/sdk/webhooks`.
 *
 * @example
 * ```ts
 * import { AmiClient } from "@ami-protocol/sdk";
 *
 * const client = new AmiClient({ apiKey: process.env.AMI_API_KEY! });
 * const creds = await client.provisionNumber({
 *   country: "ES",
 *   customer: {
 *     legalName: "Acme S.L.",
 *     taxId: "B12345678",
 *     billingEmail: "billing@acme.test",
 *     address: "Madrid, Spain",
 *     representativeName: "Ada Lovelace",
 *   },
 * });
 * const agent = client.asAgent(creds.agentToken);
 * await agent.sendSms({ to: "+34600000000", body: "hello" });
 * ```
 */

export { AmiClient } from "./client.js";
export type { AmiClientOptions } from "./client.js";

export { AmiAgent } from "./agent.js";
export type { AmiAgentOptions } from "./agent.js";

export {
  AmiError,
  AmiAuthError,
  AmiNotFoundError,
  AmiConflictError,
  AmiValidationError,
  AmiRateLimitError,
  AmiServerError,
  AmiTransportError,
  errorFromResponse,
} from "./errors.js";
export type { AmiErrorOptions } from "./errors.js";

export { verifySignature, signPayload } from "./webhooks.js";
export type { VerifySignatureOptions } from "./webhooks.js";

export * from "./types.js";
