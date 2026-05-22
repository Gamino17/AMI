/**
 * `AmiClient` — high-level client for the customer-facing surface of AMI.
 *
 * Authenticates with the customer's API key (`AMI_API_KEY`) and covers:
 *  - The full contracting flow (SIM request -> offer -> customer data ->
 *    contract -> signature -> mobile identity activation).
 *  - Administration of an active MobileIdentity (limits, webhooks, inbound
 *    SIP forwarding, agent-token rotation).
 *  - A convenience `provisionNumber()` helper that runs the whole flow in
 *    one call.
 *
 * For SMS and voice operations, get an `AmiAgent` via `client.asAgent(token)`
 * or instantiate one directly.
 */

import { AmiAgent } from "./agent.js";
import { HttpClient } from "./http.js";
import type {
  ActivatedIdentity,
  Contract,
  CreateContractInput,
  CreateWebhookInput,
  CustomerData,
  Customer,
  Limits,
  LimitsPatch,
  MobileIdentity,
  ProvisionNumberInput,
  ProvisionedNumber,
  RequestNumberInput,
  RequestNumberResult,
  SimRequest,
  SubmitCustomerDataResult,
  UsageSnapshot,
  WebhookSummary,
  WebhookWithSecret,
} from "./types.js";

export interface AmiClientOptions {
  /** The customer's API key (issued out-of-band by AMI). Required. */
  apiKey: string;
  /** Base URL of the AMI API. Defaults to `https://api.protocolami.com`. */
  baseUrl?: string;
  /** Optional custom `fetch` implementation. Defaults to global `fetch`. */
  fetch?: typeof fetch;
  /** Per-request timeout in ms. Defaults to 30000. Set 0 to disable. */
  timeoutMs?: number;
  /** Retries for transport errors and 5xx on idempotent calls. Default 2. */
  maxRetries?: number;
  /** Free-form string appended to the User-Agent header (e.g. app name). */
  userAgent?: string;
}

const DEFAULT_BASE_URL = "https://api.protocolami.com";

export class AmiClient {
  private readonly http: HttpClient;
  /** Base URL after normalisation. Exposed read-only for diagnostics. */
  readonly baseUrl: string;

  constructor(options: AmiClientOptions) {
    if (!options || !options.apiKey) {
      throw new Error("AmiClient: `apiKey` is required");
    }
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.http = new HttpClient({
      baseUrl: this.baseUrl,
      authToken: options.apiKey,
      fetch: options.fetch,
      timeoutMs: options.timeoutMs,
      maxRetries: options.maxRetries,
      userAgent: options.userAgent,
    });
  }

  // ---------------------------------------------------------------------
  // Contracting
  // ---------------------------------------------------------------------

  /**
   * Creates a SIM request and returns the immediate offer. First step of the
   * contracting flow.
   */
  async requestNumber(input: RequestNumberInput): Promise<RequestNumberResult> {
    const body: Record<string, unknown> = {
      country: input.country,
      capabilities: input.capabilities ?? ["sms", "voice"],
      simType: input.simType ?? "eSIM",
    };
    if (input.agentName || input.agentPurpose) {
      body.agent = {
        name: input.agentName,
        purpose: input.agentPurpose ?? "agent_identity",
      };
    }
    if (input.commercialConstraints) {
      body.commercialConstraints = input.commercialConstraints;
    }
    return this.http.request<RequestNumberResult>("/v1/sim-requests", {
      method: "POST",
      body,
    });
  }

  /** Fetches the current state of a SIM request by id. */
  async getSimRequest(simRequestId: string): Promise<SimRequest> {
    return this.http.request<SimRequest>(
      `/v1/sim-requests/${encodeURIComponent(simRequestId)}`,
    );
  }

  /** Cancels a SIM request before activation. */
  async cancelSimRequest(simRequestId: string, reason?: string): Promise<SimRequest> {
    return this.http.request<SimRequest>(
      `/v1/sim-requests/${encodeURIComponent(simRequestId)}/cancel`,
      { method: "POST", body: reason ? { reason } : {} },
    );
  }

  /** Accepts an offer. Required before creating a contract. */
  async acceptOffer(offerId: string): Promise<RequestNumberResult["offer"]> {
    return this.http.request<RequestNumberResult["offer"]>(
      `/v1/offers/${encodeURIComponent(offerId)}/accept`,
      { method: "POST", body: {} },
    );
  }

  /**
   * Submits the customer's legal data and binds it to a SIM request. AMI
   * creates a `Customer` record and advances the request to
   * `customer_data_submitted`.
   */
  async submitCustomerData(
    simRequestId: string,
    customer: CustomerData,
  ): Promise<SubmitCustomerDataResult> {
    return this.http.request<SubmitCustomerDataResult>(
      `/v1/sim-requests/${encodeURIComponent(simRequestId)}/customer-data`,
      { method: "POST", body: { customer } },
    );
  }

  /** Creates a contract from an accepted offer and a customer. */
  async createContract(input: CreateContractInput): Promise<Contract> {
    return this.http.request<Contract>("/v1/contracts", {
      method: "POST",
      body: input,
    });
  }

  /** Fetches a contract by id. */
  async getContract(contractId: string): Promise<Contract> {
    return this.http.request<Contract>(
      `/v1/contracts/${encodeURIComponent(contractId)}`,
    );
  }

  /**
   * Marks a contract as signed via the programmatic shortcut. The "real"
   * signature path is a hosted HTML page at `contract.signatureUrl`; this
   * method is used for tests and demos.
   */
  async mockSign(contractId: string): Promise<Contract> {
    return this.http.request<Contract>(
      `/v1/contracts/${encodeURIComponent(contractId)}/mock-sign`,
      { method: "POST", body: {} },
    );
  }

  /**
   * Activates the MobileIdentity for a signed contract and returns it
   * together with the `agentToken` (Level-2 credential).
   *
   * The `agentToken` is returned ONLY here — persist it immediately. Pass it
   * to `client.asAgent(token)` or `new AmiAgent({ agentToken })` to operate
   * the number (SMS, voice, usage).
   */
  async activateIdentity(contractId: string): Promise<ActivatedIdentity> {
    return this.http.request<ActivatedIdentity>(
      "/v1/mobile-identities/activate",
      { method: "POST", body: { contractId } },
    );
  }

  /** Fetches a MobileIdentity by id. */
  async getIdentity(mid: string): Promise<MobileIdentity> {
    return this.http.request<MobileIdentity>(
      `/v1/mobile-identities/${encodeURIComponent(mid)}`,
    );
  }

  // ---------------------------------------------------------------------
  // Identity administration (Level 1)
  // ---------------------------------------------------------------------

  /**
   * Configures the SIP endpoint where AMI forwards inbound calls for this
   * MID (typically the customer's voice engine or PBX). Pass `null` or an
   * empty string to clear the configuration; inbound calls will then be
   * rejected.
   */
  async setInboundSipUri(
    mid: string,
    sipUri: string | null,
  ): Promise<{ mid: string; inboundSipUri: string | null }> {
    return this.http.request(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/inbound-config`,
      { method: "POST", body: { inboundSipUri: sipUri } },
    );
  }

  /** Reads the current rate-limit + spending policy for a MID. */
  async getLimits(mid: string): Promise<{ mid: string; limits: Limits }> {
    return this.http.request(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/limits`,
    );
  }

  /** Patches the rate-limit + spending policy for a MID (partial update). */
  async updateLimits(
    mid: string,
    patch: LimitsPatch,
  ): Promise<{ mid: string; limits: Limits }> {
    return this.http.request(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/limits`,
      { method: "POST", body: patch },
    );
  }

  /** Reads the usage snapshot for a MID (counters, % consumed, pricing). */
  async getUsage(mid: string): Promise<UsageSnapshot> {
    return this.http.request<UsageSnapshot>(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/usage`,
    );
  }

  /**
   * Hard-rotates the agent token for a MID. The previous token is
   * invalidated immediately; the new token is returned ONCE.
   */
  async rotateAgentToken(mid: string): Promise<{
    mid: string;
    agentToken: string;
    revokedPrevious: number;
    rotatedAt: string;
  }> {
    return this.http.request(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/rotate-token`,
      { method: "POST", body: {} },
    );
  }

  // ---------------------------------------------------------------------
  // Outbound webhooks (Level 1)
  // ---------------------------------------------------------------------

  /**
   * Registers a webhook URL for a MID. AMI will POST `event`/`mid`/`data`
   * payloads to this URL signed with `HMAC-SHA256(secret, rawBody)` in the
   * `X-Ami-Signature` header. The `secret` is returned ONLY here; persist
   * it immediately and use `verifySignature(...)` from
   * `@ami-protocol/sdk/webhooks` in your handler.
   */
  async createWebhook(
    mid: string,
    input: CreateWebhookInput,
  ): Promise<WebhookWithSecret> {
    const body = { url: input.url, events: input.events ?? ["*"] };
    return this.http.request<WebhookWithSecret>(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/webhooks`,
      { method: "POST", body },
    );
  }

  /** Lists every webhook registered for a MID. */
  async listWebhooks(mid: string): Promise<WebhookSummary[]> {
    const out = await this.http.request<{ webhooks: WebhookSummary[]; count: number }>(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/webhooks`,
    );
    return out.webhooks;
  }

  /** Deletes a webhook by id. */
  async deleteWebhook(mid: string, webhookId: string): Promise<void> {
    await this.http.request(
      `/v1/mobile-identities/${encodeURIComponent(mid)}/webhooks/${encodeURIComponent(webhookId)}/delete`,
      { method: "POST", body: {} },
    );
  }

  // ---------------------------------------------------------------------
  // Conveniences
  // ---------------------------------------------------------------------

  /**
   * Creates an `AmiAgent` bound to the given agent token. Reuses this
   * client's `baseUrl`, `fetch`, `userAgent` and retry/timeout settings.
   */
  asAgent(agentToken: string): AmiAgent {
    return new AmiAgent({
      agentToken,
      baseUrl: this.baseUrl,
    });
  }

  /**
   * One-shot helper that walks the entire contracting flow and returns the
   * final agent credentials. Uses `mockSign` for the signature step, which
   * is the standard path for non-interactive provisioning in v1.
   *
   * Returned object contains every intermediate resource so callers can log
   * them or persist what they need.
   */
  async provisionNumber(input: ProvisionNumberInput): Promise<ProvisionedNumber> {
    const { simRequest, offer } = await this.requestNumber({
      country: input.country,
      capabilities: input.capabilities,
      simType: input.simType,
      agentName: input.agentName,
      agentPurpose: input.agentPurpose,
      commercialConstraints: input.commercialConstraints,
    });
    await this.acceptOffer(offer.id);
    const submission = await this.submitCustomerData(simRequest.id, input.customer);
    const contract = await this.createContract({
      offerId: offer.id,
      customerId: submission.customer.id,
    });
    await this.mockSign(contract.id);
    const identity = await this.activateIdentity(contract.id);

    return {
      simRequest: submission.simRequest,
      offer,
      customer: submission.customer,
      contract,
      identity,
      agentToken: identity.agentToken,
      mid: identity.id,
      phoneNumber: identity.phoneNumber,
    };
  }
}

// Re-export the typed alias for convenience.
export type { Customer };
