/**
 * `AmiAgent` — operates a MobileIdentity using its scoped agent token.
 *
 * Authenticates with the `amiagt_live_<hex>` agent token returned by
 * `client.activateIdentity(...)` or `client.rotateAgentToken(...)`. Covers
 * the SMS and voice surface (`/v1/agent/*`) plus the self-scoped usage view.
 *
 * Out of scope here: contracting, limits administration, webhook registration.
 * Those require the customer-level API key and live on `AmiClient`.
 */

import { HttpClient } from "./http.js";
import type {
  AgentSelf,
  Call,
  ListCallsInput,
  ListSmsInput,
  PlaceCallInput,
  SendSmsInput,
  SmsMessage,
  UsageSnapshot,
} from "./types.js";

export interface AmiAgentOptions {
  /** The scoped agent token (`amiagt_live_...`). Required. */
  agentToken: string;
  /** Base URL of the AMI API. Defaults to `https://api.protocolami.com`. */
  baseUrl?: string;
  /** Optional custom `fetch` implementation. Defaults to global `fetch`. */
  fetch?: typeof fetch;
  /** Per-request timeout in ms. Defaults to 30000. Set 0 to disable. */
  timeoutMs?: number;
  /** Retries for transport errors and 5xx on idempotent calls. Default 2. */
  maxRetries?: number;
  /** Free-form string appended to the User-Agent header. */
  userAgent?: string;
}

const DEFAULT_BASE_URL = "https://api.protocolami.com";

export class AmiAgent {
  private readonly http: HttpClient;
  /** Base URL after normalisation. Exposed read-only for diagnostics. */
  readonly baseUrl: string;

  constructor(options: AmiAgentOptions) {
    if (!options || !options.agentToken) {
      throw new Error("AmiAgent: `agentToken` is required");
    }
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.http = new HttpClient({
      baseUrl: this.baseUrl,
      authToken: options.agentToken,
      fetch: options.fetch,
      timeoutMs: options.timeoutMs,
      maxRetries: options.maxRetries,
      userAgent: options.userAgent,
    });
  }

  // ---------------------------------------------------------------------
  // Self
  // ---------------------------------------------------------------------

  /** Returns metadata about the MobileIdentity that owns the current token. */
  async self(signal?: AbortSignal): Promise<AgentSelf> {
    return this.http.request<AgentSelf>("/v1/agent/self", { signal });
  }

  /** Returns the usage snapshot for this MID (counters, %, pricing). */
  async usage(signal?: AbortSignal): Promise<UsageSnapshot> {
    return this.http.request<UsageSnapshot>("/v1/agent/usage", { signal });
  }

  // ---------------------------------------------------------------------
  // SMS
  // ---------------------------------------------------------------------

  /**
   * Sends an outbound SMS from this MID. The message is enqueued; status
   * transitions through `queued -> sent -> delivered` (DLR-driven).
   *
   * Throws `AmiRateLimitError` (HTTP 429) when a limit fires
   * (`sms_hourly_limit_exceeded`, `sms_daily_limit_exceeded`,
   * `monthly_budget_exceeded`, `country_not_allowed`). Inspect `.reason`.
   */
  async sendSms(input: SendSmsInput, signal?: AbortSignal): Promise<SmsMessage> {
    return this.http.request<SmsMessage>("/v1/agent/sms/send", {
      method: "POST",
      body: input,
      signal,
    });
  }

  /** Lists SMS for this MID, most recent first. */
  async listSms(input: ListSmsInput = {}, signal?: AbortSignal): Promise<SmsMessage[]> {
    const out = await this.http.request<{ messages: SmsMessage[]; count: number }>(
      "/v1/agent/sms",
      {
        query: {
          limit: input.limit,
          direction: input.direction,
        },
        signal,
      },
    );
    return out.messages;
  }

  // ---------------------------------------------------------------------
  // Voice
  // ---------------------------------------------------------------------

  /**
   * Originates an outbound call (bridge-by-API). AMI dials the PSTN and
   * bridges the leg over SIP to `callbackSipUri` (typically the customer's
   * real-time voice engine or PBX). AMI carries the audio pipe; the agent
   * "brain" runs on the customer's endpoint.
   */
  async placeCall(input: PlaceCallInput, signal?: AbortSignal): Promise<Call> {
    return this.http.request<Call>("/v1/agent/calls/place", {
      method: "POST",
      body: input,
      signal,
    });
  }

  /** Lists calls for this MID, most recent first. */
  async listCalls(input: ListCallsInput = {}, signal?: AbortSignal): Promise<Call[]> {
    const out = await this.http.request<{ calls: Call[]; count: number }>(
      "/v1/agent/calls",
      {
        query: {
          limit: input.limit,
          direction: input.direction,
        },
        signal,
      },
    );
    return out.calls;
  }

  /** Fetches a single call by id (must belong to this MID). */
  async getCall(callId: string, signal?: AbortSignal): Promise<Call> {
    return this.http.request<Call>(
      `/v1/agent/calls/${encodeURIComponent(callId)}`,
      { signal },
    );
  }

  /** Terminates a live call. Idempotent on already-completed calls. */
  async hangupCall(callId: string, signal?: AbortSignal): Promise<Call> {
    return this.http.request<Call>(
      `/v1/agent/calls/${encodeURIComponent(callId)}/hangup`,
      { method: "POST", body: {}, signal },
    );
  }
}
