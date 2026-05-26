/**
 * Public type definitions for AMI resources.
 *
 * All field names use camelCase. The SDK transparently converts between
 * camelCase (TypeScript) and snake_case (the JSON wire format used by the
 * AMI HTTP API).
 */

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

/** Two-letter ISO 3166-1 alpha-2 country code, e.g. "ES", "FR", "DE". */
export type CountryCode = string;

/** Capabilities a MobileIdentity can expose. */
export type Capability = "sms" | "voice" | "data";

/** SIM form factor. */
export type SimType = "eSIM" | "pSIM";

/** Direction of a message or call. */
export type Direction = "outbound" | "inbound";

/** SMS message lifecycle states. */
export type SmsStatus = "queued" | "sent" | "delivered" | "failed" | "received";

/** Call lifecycle states. */
export type CallStatus =
  | "initiated"
  | "ringing"
  | "in_progress"
  | "completed"
  | "failed"
  | "no_answer"
  | "busy"
  | "cancelled";

/** SIMRequest lifecycle states (see SPEC.md §7.6). */
export type SimRequestStatus =
  | "requested"
  | "offer_created"
  | "offer_accepted"
  | "customer_data_submitted"
  | "signature_pending"
  | "signed"
  | "provisioning"
  | "active"
  | "rejected"
  | "cancelled"
  | "failed";

/** Webhook event types AMI can dispatch. */
export type WebhookEvent =
  | "sms.inbound"
  | "sms.delivered"
  | "sms.failed"
  | "call.inbound"
  | "call.completed"
  | "call.failed"
  | "*";

// ---------------------------------------------------------------------------
// Contracting resources
// ---------------------------------------------------------------------------

/** Agent metadata attached to a SIMRequest. */
export interface AgentSpec {
  /** Human-readable name for the agent (e.g. "support-bot-eu"). */
  name?: string;
  /** Free-form purpose tag (e.g. "agent_identity"). */
  purpose?: string;
}

/** Commercial constraints the customer applies when requesting an offer. */
export interface CommercialConstraints {
  maxMonthlyPrice?: number;
  currency?: string;
}

export interface SimRequest {
  id: string;
  status: SimRequestStatus;
  country: CountryCode;
  simType: SimType;
  capabilities: Capability[];
  agent?: AgentSpec;
  customerId?: string | null;
  offerId?: string | null;
  mobileIdentityId?: string | null;
  commercialConstraints?: CommercialConstraints;
  createdAt: string;
  updatedAt?: string;
}

export interface Offer {
  id: string;
  simRequestId: string;
  status: "offer_created" | "offer_accepted" | "expired";
  country: CountryCode;
  simType: SimType;
  capabilities: Capability[];
  monthlyPrice: number;
  setupFee: number;
  currency: string;
  requiresContract: boolean;
  requiresCustomerData: boolean;
  expiresAt: string;
  createdAt: string;
  acceptedAt?: string;
}

/** Customer / KYB data attached to a contract. */
export interface CustomerData {
  legalName: string;
  taxId: string;
  billingEmail: string;
  address: string;
  representativeName: string;
  /**
   * E.164 phone number of the legal representative. Optional but
   * recommended: AMI uses it to deliver the KYC verification link by SMS
   * in addition to the billing email.
   */
  representativePhone?: string;
}

export interface Customer extends CustomerData {
  id: string;
  status: string;
  createdAt: string;
}

export interface Contract {
  id: string;
  status: "draft" | "signature_pending" | "signed" | "expired" | "cancelled";
  offerId: string;
  customerId: string;
  simRequestId?: string;
  signatureUrl: string;
  createdAt: string;
  expiresAt: string;
  signedAt?: string;
}

export interface MobileIdentity {
  id: string;
  status: "provisioning" | "active" | "suspended" | "terminated";
  phoneNumber: string;
  simType: SimType;
  capabilities: Capability[];
  contractId: string;
  customerId: string;
  simRequestId?: string;
  providerActivationId?: string;
  esimQrUrl?: string;
  activatedAt?: string;
  inboundSipUri?: string | null;
}

/** KYC lifecycle states. */
export type KycStatus =
  | "pending"
  | "submitted"
  | "in_review"
  | "verified"
  | "rejected"
  | "expired";

/**
 * Per-channel hint of the KYC invitation that AMI dispatches when the
 * verification is initiated. The shape is intentionally loose — the SDK
 * exposes it verbatim so callers can log or surface it without coupling
 * to the backend's notification adapter internals.
 */
export interface KycNotificationStatus {
  email?: { status: string; [key: string]: unknown } | null;
  sms?: { status: string; [key: string]: unknown } | null;
  [channel: string]: { status: string; [key: string]: unknown } | null | undefined;
}

/** Result of `client.initiateKyc()`. */
export interface KycInitiationResult {
  /** AMI-side identifier of the KYC verification record. */
  kycId: string;
  /** Current KYC status. Freshly initiated KYCs are `"pending"`. */
  status: KycStatus;
  /**
   * Public URL where the legal representative uploads their ID + selfie.
   * Send this to the human (email / SMS / WhatsApp / share link).
   */
  verificationUrl: string;
  /** Email AMI delivered the invitation to (the customer's billing email). */
  repEmail?: string;
  /** ISO-8601 expiry of the verification token. */
  expiresAt?: string;
  /** Out-of-band notification dispatch hint (email / SMS). */
  notification?: KycNotificationStatus | null;
  /**
   * `true` when a KYC for this SIM request already existed and was reused
   * instead of creating a new one. `initiateKyc` is idempotent.
   */
  alreadyExisted?: boolean;
}

/** Activation response. The `agentToken` is returned ONLY on this call. */
export interface ActivatedIdentity extends MobileIdentity {
  /** Bearer token scoped to this MID. Persist it immediately; AMI does not
   *  return it again. Use it as `agentToken` for `AmiAgent`. */
  agentToken: string;
  /** Short human-readable hint reminding the caller to store the token. */
  agentTokenHint?: string;
}

// ---------------------------------------------------------------------------
// Operations resources
// ---------------------------------------------------------------------------

export interface SmsMessage {
  id: string;
  mid: string;
  direction: Direction;
  from: string;
  to: string;
  body: string;
  status: SmsStatus;
  createdAt: string;
  deliveredAt?: string | null;
  telcoRef?: string | null;
}

export interface Call {
  id: string;
  mid: string;
  direction: Direction;
  from: string;
  to: string;
  callbackSipUri?: string;
  status: CallStatus;
  createdAt: string;
  startedAt?: string | null;
  endedAt?: string | null;
  durationSec?: number | null;
  hangupCause?: string | null;
  telcoRef?: string | null;
}

// ---------------------------------------------------------------------------
// Governance: limits, usage, webhooks
// ---------------------------------------------------------------------------

export interface Limits {
  smsPerHour: number;
  smsPerDay: number;
  callsPerHour: number;
  callsPerDay: number;
  monthlyBudgetEur: number;
  allowedCountryPrefixes: string[];
}

export type LimitsPatch = Partial<Limits>;

export interface UsageCounters {
  smsCountThisHour: number;
  smsCountToday: number;
  callsCountThisHour: number;
  callsCountToday: number;
  callsMinutesThisMonth: number;
  spendThisMonthEur: number;
  lastResetAtHour: string;
  lastResetAtDay: string;
  lastResetAtMonth: string;
}

export interface Pricing {
  smsEur: number;
  callPerMinEur: number;
}

export interface ConsumedPct {
  smsHour: number | null;
  smsDay: number | null;
  callsHour: number | null;
  callsDay: number | null;
  monthlyBudget: number | null;
}

export interface UsageSnapshot {
  usage: UsageCounters;
  limits: Limits;
  pricing: Pricing;
  consumedPct: ConsumedPct;
}

export interface WebhookSummary {
  id: string;
  mid: string;
  url: string;
  events: WebhookEvent[];
  status: "active" | "disabled";
  createdAt: string;
  lastDeliveryAt?: string | null;
  lastDeliveryStatus?: "ok" | "failed" | null;
  failureCount: number;
  /** Visible prefix of the webhook secret (e.g. "whsec_ab12cd"). */
  secretPrefix: string;
}

/** Response of `createWebhook`. The full `secret` is returned ONLY here. */
export interface WebhookWithSecret extends WebhookSummary {
  /** Persist this. Reconstruct HMAC-SHA256(secret, rawBody) and compare against
   *  the `X-Ami-Signature` header on every delivery. */
  secret: string;
  /** Short human-readable hint reminding the caller to store the secret. */
  secretHint?: string;
}

// ---------------------------------------------------------------------------
// Agent self-info
// ---------------------------------------------------------------------------

export interface AgentSelf {
  mobileIdentityId: string;
  customerId: string;
  phoneNumber: string;
  capabilities: Capability[];
  tokenPrefix: string;
  tokenCreatedAt: string;
  status: "active";
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

export interface RequestNumberInput {
  country: CountryCode;
  capabilities?: Capability[];
  simType?: SimType;
  /** Friendly name attached to the SIMRequest (mapped to `agent.name`). */
  agentName?: string;
  /** Optional purpose tag (mapped to `agent.purpose`). */
  agentPurpose?: string;
  commercialConstraints?: CommercialConstraints;
}

export interface RequestNumberResult {
  simRequest: SimRequest;
  offer: Offer;
}

export interface SubmitCustomerDataResult {
  simRequest: SimRequest;
  customer: Customer;
}

export interface CreateContractInput {
  offerId: string;
  customerId: string;
}

export interface CreateWebhookInput {
  url: string;
  events?: WebhookEvent[];
}

export interface SendSmsInput {
  to: string;
  body: string;
}

export interface PlaceCallInput {
  to: string;
  callbackSipUri: string;
}

export interface ListSmsInput {
  limit?: number;
  direction?: Direction;
}

export interface ListCallsInput {
  limit?: number;
  direction?: Direction;
}

/** Full provisioning result returned by `client.provisionNumber()`. */
export interface ProvisionedNumber {
  simRequest: SimRequest;
  offer: Offer;
  customer: Customer;
  contract: Contract;
  identity: ActivatedIdentity;
  agentToken: string;
  mid: string;
  phoneNumber: string;
}

export interface ProvisionNumberInput {
  country: CountryCode;
  customer: CustomerData;
  capabilities?: Capability[];
  simType?: SimType;
  agentName?: string;
  agentPurpose?: string;
  commercialConstraints?: CommercialConstraints;
}
