import { describe, it, expect, beforeEach } from "vitest";
import { AmiClient, AmiAuthError, AmiNotFoundError, AmiConflictError, AmiValidationError } from "../src/index.js";
import { createMockFetch, jsonResponse } from "./_mock.js";

describe("AmiClient", () => {
  let mock: ReturnType<typeof createMockFetch>;
  let client: AmiClient;

  beforeEach(() => {
    mock = createMockFetch();
    client = new AmiClient({
      apiKey: "ami_test_key",
      baseUrl: "https://api.test",
      fetch: mock.fetch,
      maxRetries: 0,
    });
  });

  it("rejects construction without apiKey", () => {
    expect(() => new AmiClient({ apiKey: "" })).toThrow(/apiKey/);
  });

  it("requestNumber posts snake_case body and parses camelCase response", async () => {
    mock.on("POST", "/v1/sim-requests", (req) => {
      expect(req.headers.authorization).toBe("Bearer ami_test_key");
      expect(req.headers["content-type"]).toBe("application/json");
      expect(req.body).toEqual({
        country: "ES",
        capabilities: ["sms", "voice"],
        sim_type: "eSIM",
        agent: { name: "agent01", purpose: "agent_identity" },
      });
      return jsonResponse(201, {
        sim_request: {
          id: "simreq_1",
          status: "offer_created",
          country: "ES",
          sim_type: "eSIM",
          capabilities: ["sms", "voice"],
          created_at: "2026-05-22T00:00:00Z",
        },
        offer: {
          id: "offer_1",
          sim_request_id: "simreq_1",
          status: "offer_created",
          country: "ES",
          sim_type: "eSIM",
          capabilities: ["sms", "voice"],
          monthly_price: 8.9,
          setup_fee: 5,
          currency: "EUR",
          requires_contract: true,
          requires_customer_data: true,
          expires_at: "2026-05-29T23:59:59Z",
          created_at: "2026-05-22T00:00:00Z",
        },
      });
    });

    const out = await client.requestNumber({
      country: "ES",
      agentName: "agent01",
    });
    expect(out.simRequest.id).toBe("simreq_1");
    expect(out.simRequest.simType).toBe("eSIM");
    expect(out.offer.monthlyPrice).toBe(8.9);
    expect(out.offer.requiresContract).toBe(true);
  });

  it("acceptOffer / submitCustomerData / createContract / mockSign / activateIdentity flow", async () => {
    mock.on("POST", "/v1/offers/offer_1/accept", () =>
      jsonResponse(200, { id: "offer_1", status: "offer_accepted" }),
    );
    mock.on("POST", "/v1/sim-requests/simreq_1/customer-data", (req) => {
      expect(req.body).toEqual({
        customer: {
          legal_name: "Acme",
          tax_id: "B1",
          billing_email: "a@b.c",
          address: "Madrid",
          representative_name: "Ada",
        },
      });
      return jsonResponse(201, {
        sim_request: { id: "simreq_1", status: "customer_data_submitted" },
        customer: {
          id: "customer_1",
          status: "created",
          legal_name: "Acme",
          tax_id: "B1",
          billing_email: "a@b.c",
          address: "Madrid",
          representative_name: "Ada",
          created_at: "2026-05-22T00:00:00Z",
        },
      });
    });
    mock.on("POST", "/v1/contracts", (req) => {
      expect(req.body).toEqual({ offer_id: "offer_1", customer_id: "customer_1" });
      return jsonResponse(201, {
        id: "contract_1",
        status: "signature_pending",
        offer_id: "offer_1",
        customer_id: "customer_1",
        signature_url: "https://api.test/v1/sign/contract_1",
        created_at: "2026-05-22T00:00:00Z",
        expires_at: "2026-05-29T00:00:00Z",
      });
    });
    mock.on("POST", "/v1/contracts/contract_1/mock-sign", () =>
      jsonResponse(200, {
        id: "contract_1",
        status: "signed",
        offer_id: "offer_1",
        customer_id: "customer_1",
        signature_url: "https://api.test/v1/sign/contract_1",
        created_at: "2026-05-22T00:00:00Z",
        expires_at: "2026-05-29T00:00:00Z",
        signed_at: "2026-05-22T00:00:01Z",
      }),
    );
    mock.on("POST", "/v1/mobile-identities/activate", (req) => {
      expect(req.body).toEqual({ contract_id: "contract_1" });
      return jsonResponse(201, {
        id: "mid_1",
        status: "active",
        phone_number: "+34600000000",
        sim_type: "eSIM",
        capabilities: ["sms", "voice"],
        contract_id: "contract_1",
        customer_id: "customer_1",
        activated_at: "2026-05-22T00:00:02Z",
        agent_token: "amiagt_live_deadbeef",
        agent_token_hint: "store it",
      });
    });

    const accepted = await client.acceptOffer("offer_1");
    expect(accepted.status).toBe("offer_accepted");

    const sub = await client.submitCustomerData("simreq_1", {
      legalName: "Acme",
      taxId: "B1",
      billingEmail: "a@b.c",
      address: "Madrid",
      representativeName: "Ada",
    });
    expect(sub.customer.id).toBe("customer_1");
    expect(sub.customer.legalName).toBe("Acme");

    const contract = await client.createContract({
      offerId: "offer_1",
      customerId: "customer_1",
    });
    expect(contract.id).toBe("contract_1");
    expect(contract.signatureUrl).toContain("/v1/sign/");

    await client.mockSign("contract_1");

    const identity = await client.activateIdentity("contract_1");
    expect(identity.id).toBe("mid_1");
    expect(identity.phoneNumber).toBe("+34600000000");
    expect(identity.agentToken).toBe("amiagt_live_deadbeef");
  });

  it("provisionNumber runs the full flow", async () => {
    mock.on("POST", "/v1/sim-requests", () =>
      jsonResponse(201, {
        sim_request: {
          id: "simreq_x",
          status: "offer_created",
          country: "ES",
          sim_type: "eSIM",
          capabilities: ["sms"],
          created_at: "t",
        },
        offer: {
          id: "offer_x",
          sim_request_id: "simreq_x",
          status: "offer_created",
          country: "ES",
          sim_type: "eSIM",
          capabilities: ["sms"],
          monthly_price: 8.9,
          setup_fee: 5,
          currency: "EUR",
          requires_contract: true,
          requires_customer_data: true,
          expires_at: "t",
          created_at: "t",
        },
      }),
    );
    mock.on("POST", "/v1/offers/offer_x/accept", () =>
      jsonResponse(200, { id: "offer_x", status: "offer_accepted" }),
    );
    mock.on("POST", "/v1/sim-requests/simreq_x/customer-data", () =>
      jsonResponse(201, {
        sim_request: { id: "simreq_x", status: "customer_data_submitted" },
        customer: {
          id: "customer_x",
          status: "created",
          legal_name: "Acme",
          tax_id: "B1",
          billing_email: "a@b.c",
          address: "Madrid",
          representative_name: "Ada",
          created_at: "t",
        },
      }),
    );
    mock.on("POST", "/v1/contracts", () =>
      jsonResponse(201, {
        id: "contract_x",
        status: "signature_pending",
        offer_id: "offer_x",
        customer_id: "customer_x",
        signature_url: "u",
        created_at: "t",
        expires_at: "t",
      }),
    );
    mock.on("POST", "/v1/contracts/contract_x/mock-sign", () =>
      jsonResponse(200, {
        id: "contract_x",
        status: "signed",
        offer_id: "offer_x",
        customer_id: "customer_x",
        signature_url: "u",
        created_at: "t",
        expires_at: "t",
        signed_at: "t",
      }),
    );
    mock.on("POST", "/v1/mobile-identities/activate", () =>
      jsonResponse(201, {
        id: "mid_x",
        status: "active",
        phone_number: "+34600111222",
        sim_type: "eSIM",
        capabilities: ["sms"],
        contract_id: "contract_x",
        customer_id: "customer_x",
        agent_token: "amiagt_live_token",
      }),
    );

    const out = await client.provisionNumber({
      country: "ES",
      capabilities: ["sms"],
      customer: {
        legalName: "Acme",
        taxId: "B1",
        billingEmail: "a@b.c",
        address: "Madrid",
        representativeName: "Ada",
      },
    });
    expect(out.mid).toBe("mid_x");
    expect(out.phoneNumber).toBe("+34600111222");
    expect(out.agentToken).toBe("amiagt_live_token");
    // Verifies order: 6 requests in canonical order.
    expect(mock.requests.map((r) => r.pathname)).toEqual([
      "/v1/sim-requests",
      "/v1/offers/offer_x/accept",
      "/v1/sim-requests/simreq_x/customer-data",
      "/v1/contracts",
      "/v1/contracts/contract_x/mock-sign",
      "/v1/mobile-identities/activate",
    ]);
  });

  it("createWebhook camelizes secret prefix and exposes secret once", async () => {
    mock.on("POST", "/v1/mobile-identities/mid_1/webhooks", (req) => {
      expect(req.body).toEqual({
        url: "https://hook.test",
        events: ["sms.inbound"],
      });
      return jsonResponse(201, {
        id: "wh_1",
        mid: "mid_1",
        url: "https://hook.test",
        events: ["sms.inbound"],
        status: "active",
        created_at: "t",
        last_delivery_at: null,
        last_delivery_status: null,
        failure_count: 0,
        secret_prefix: "whsec_aaaa",
        secret: "whsec_full_secret_value",
        secret_hint: "keep it safe",
      });
    });
    const wh = await client.createWebhook("mid_1", {
      url: "https://hook.test",
      events: ["sms.inbound"],
    });
    expect(wh.secret).toBe("whsec_full_secret_value");
    expect(wh.secretPrefix).toBe("whsec_aaaa");
    expect(wh.failureCount).toBe(0);
  });

  it("listWebhooks unwraps the envelope", async () => {
    mock.on("GET", "/v1/mobile-identities/mid_1/webhooks", () =>
      jsonResponse(200, {
        webhooks: [
          {
            id: "wh_1",
            mid: "mid_1",
            url: "https://h",
            events: ["*"],
            status: "active",
            created_at: "t",
            last_delivery_at: null,
            last_delivery_status: null,
            failure_count: 0,
            secret_prefix: "whsec_aaaa",
          },
        ],
        count: 1,
      }),
    );
    const list = await client.listWebhooks("mid_1");
    expect(list).toHaveLength(1);
    expect(list[0]!.id).toBe("wh_1");
  });

  it("updateLimits and setInboundSipUri snake-case their payloads", async () => {
    mock.on("POST", "/v1/mobile-identities/mid_1/limits", (req) => {
      expect(req.body).toEqual({
        sms_per_day: 1000,
        monthly_budget_eur: 50,
      });
      return jsonResponse(200, {
        mid: "mid_1",
        limits: {
          sms_per_hour: 60,
          sms_per_day: 1000,
          calls_per_hour: 20,
          calls_per_day: 200,
          monthly_budget_eur: 50,
          allowed_country_prefixes: ["+34"],
        },
      });
    });
    const out = await client.updateLimits("mid_1", { smsPerDay: 1000, monthlyBudgetEur: 50 });
    expect(out.limits.smsPerDay).toBe(1000);
    expect(out.limits.monthlyBudgetEur).toBe(50);

    mock.on("POST", "/v1/mobile-identities/mid_1/inbound-config", (req) => {
      expect(req.body).toEqual({ inbound_sip_uri: "sip:agent@host.example" });
      return jsonResponse(200, {
        mid: "mid_1",
        inbound_sip_uri: "sip:agent@host.example",
      });
    });
    const sip = await client.setInboundSipUri("mid_1", "sip:agent@host.example");
    expect(sip.inboundSipUri).toBe("sip:agent@host.example");
  });

  it("rotateAgentToken returns the new plain token", async () => {
    mock.on("POST", "/v1/mobile-identities/mid_1/rotate-token", () =>
      jsonResponse(200, {
        mid: "mid_1",
        agent_token: "amiagt_live_new",
        revoked_previous: 1,
        rotated_at: "t",
      }),
    );
    const out = await client.rotateAgentToken("mid_1");
    expect(out.agentToken).toBe("amiagt_live_new");
    expect(out.revokedPrevious).toBe(1);
  });

  it("maps 401 -> AmiAuthError with reason", async () => {
    mock.on("GET", "/v1/sim-requests/simreq_1", () =>
      jsonResponse(401, { error: "unauthorized" }),
    );
    await expect(client.getSimRequest("simreq_1")).rejects.toMatchObject({
      name: "AmiAuthError",
      statusCode: 401,
      reason: "unauthorized",
    });
    await expect(client.getSimRequest("simreq_1")).rejects.toBeInstanceOf(AmiAuthError);
  });

  it("maps 404 -> AmiNotFoundError", async () => {
    mock.on("GET", "/v1/sim-requests/missing", () =>
      jsonResponse(404, { error: "sim_request_not_found" }),
    );
    await expect(client.getSimRequest("missing")).rejects.toBeInstanceOf(AmiNotFoundError);
  });

  it("maps 409 -> AmiConflictError and 400 -> AmiValidationError", async () => {
    mock.on("POST", "/v1/offers/x/accept", () =>
      jsonResponse(409, { error: "offer_not_acceptable", status: "expired" }),
    );
    await expect(client.acceptOffer("x")).rejects.toBeInstanceOf(AmiConflictError);

    mock.on("POST", "/v1/sim-requests", () =>
      jsonResponse(400, { error: "unsupported_country", country: "ZZ" }),
    );
    await expect(client.requestNumber({ country: "ZZ" })).rejects.toBeInstanceOf(
      AmiValidationError,
    );
  });

  it("asAgent returns an AmiAgent bound to the same baseUrl", () => {
    const agent = client.asAgent("amiagt_live_tok");
    expect(agent.baseUrl).toBe("https://api.test");
  });
});
