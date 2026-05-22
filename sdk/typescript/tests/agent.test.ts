import { describe, it, expect, beforeEach } from "vitest";
import { AmiAgent, AmiRateLimitError, AmiTransportError } from "../src/index.js";
import { createMockFetch, jsonResponse } from "./_mock.js";

describe("AmiAgent", () => {
  let mock: ReturnType<typeof createMockFetch>;
  let agent: AmiAgent;

  beforeEach(() => {
    mock = createMockFetch();
    agent = new AmiAgent({
      agentToken: "amiagt_live_test",
      baseUrl: "https://api.test",
      fetch: mock.fetch,
      maxRetries: 0,
    });
  });

  it("rejects construction without an agentToken", () => {
    expect(() => new AmiAgent({ agentToken: "" })).toThrow(/agentToken/);
  });

  it("self() returns camelCased AgentSelf", async () => {
    mock.on("GET", "/v1/agent/self", (req) => {
      expect(req.headers.authorization).toBe("Bearer amiagt_live_test");
      return jsonResponse(200, {
        mobile_identity_id: "mid_1",
        customer_id: "customer_1",
        phone_number: "+34600",
        capabilities: ["sms", "voice"],
        token_prefix: "amiagt_live_test",
        token_created_at: "t",
        status: "active",
      });
    });
    const me = await agent.self();
    expect(me.mobileIdentityId).toBe("mid_1");
    expect(me.phoneNumber).toBe("+34600");
    expect(me.tokenCreatedAt).toBe("t");
  });

  it("sendSms snake-cases body and parses response", async () => {
    mock.on("POST", "/v1/agent/sms/send", (req) => {
      expect(req.body).toEqual({ to: "+34600", body: "hola" });
      return jsonResponse(201, {
        id: "msg_1",
        mid: "mid_1",
        direction: "outbound",
        from: "+34601",
        to: "+34600",
        body: "hola",
        status: "queued",
        created_at: "t",
        delivered_at: null,
        telco_ref: null,
      });
    });
    const msg = await agent.sendSms({ to: "+34600", body: "hola" });
    expect(msg.id).toBe("msg_1");
    expect(msg.status).toBe("queued");
    expect(msg.telcoRef).toBeNull();
  });

  it("listSms forwards query params snake-cased and unwraps envelope", async () => {
    mock.on("GET", "/v1/agent/sms", (req) => {
      expect(req.query.limit).toBe("5");
      expect(req.query.direction).toBe("outbound");
      return jsonResponse(200, {
        messages: [
          {
            id: "msg_1",
            mid: "mid_1",
            direction: "outbound",
            from: "+34601",
            to: "+34600",
            body: "hi",
            status: "delivered",
            created_at: "t",
            delivered_at: "t",
            telco_ref: null,
          },
        ],
        count: 1,
      });
    });
    const list = await agent.listSms({ limit: 5, direction: "outbound" });
    expect(list).toHaveLength(1);
    expect(list[0]!.direction).toBe("outbound");
  });

  it("placeCall + hangupCall", async () => {
    mock.on("POST", "/v1/agent/calls/place", (req) => {
      expect(req.body).toEqual({
        to: "+34600",
        callback_sip_uri: "sip:engine@host.example",
      });
      return jsonResponse(201, {
        id: "call_1",
        mid: "mid_1",
        direction: "outbound",
        from: "+34601",
        to: "+34600",
        callback_sip_uri: "sip:engine@host.example",
        status: "initiated",
        created_at: "t",
      });
    });
    const call = await agent.placeCall({
      to: "+34600",
      callbackSipUri: "sip:engine@host.example",
    });
    expect(call.id).toBe("call_1");
    expect(call.callbackSipUri).toBe("sip:engine@host.example");

    mock.on("POST", "/v1/agent/calls/call_1/hangup", () =>
      jsonResponse(200, {
        id: "call_1",
        mid: "mid_1",
        direction: "outbound",
        from: "+34601",
        to: "+34600",
        status: "completed",
        created_at: "t",
        ended_at: "t",
        duration_sec: 12,
      }),
    );
    const ended = await agent.hangupCall("call_1");
    expect(ended.status).toBe("completed");
    expect(ended.durationSec).toBe(12);
  });

  it("listCalls + getCall", async () => {
    mock.on("GET", "/v1/agent/calls", () =>
      jsonResponse(200, {
        calls: [
          {
            id: "c1",
            mid: "mid_1",
            direction: "inbound",
            from: "+34600",
            to: "+34601",
            status: "completed",
            created_at: "t",
            duration_sec: 5,
          },
        ],
        count: 1,
      }),
    );
    const list = await agent.listCalls({ limit: 1, direction: "inbound" });
    expect(list).toHaveLength(1);
    expect(list[0]!.durationSec).toBe(5);

    mock.on("GET", "/v1/agent/calls/c1", () =>
      jsonResponse(200, {
        id: "c1",
        mid: "mid_1",
        direction: "inbound",
        from: "+34600",
        to: "+34601",
        status: "completed",
        created_at: "t",
        duration_sec: 5,
      }),
    );
    const one = await agent.getCall("c1");
    expect(one.id).toBe("c1");
  });

  it("usage() returns nested snapshot camelCased", async () => {
    mock.on("GET", "/v1/agent/usage", () =>
      jsonResponse(200, {
        usage: {
          sms_count_this_hour: 1,
          sms_count_today: 2,
          calls_count_this_hour: 0,
          calls_count_today: 0,
          calls_minutes_this_month: 0,
          spend_this_month_eur: 0.1,
          last_reset_at_hour: "t",
          last_reset_at_day: "t",
          last_reset_at_month: "t",
        },
        limits: {
          sms_per_hour: 60,
          sms_per_day: 500,
          calls_per_hour: 20,
          calls_per_day: 200,
          monthly_budget_eur: 100,
          allowed_country_prefixes: ["+34"],
        },
        pricing: { sms_eur: 0.05, call_per_min_eur: 0.04 },
        consumed_pct: {
          sms_hour: 1.67,
          sms_day: 0.4,
          calls_hour: 0,
          calls_day: 0,
          monthly_budget: 0.1,
        },
      }),
    );
    const u = await agent.usage();
    expect(u.usage.smsCountToday).toBe(2);
    expect(u.limits.smsPerDay).toBe(500);
    expect(u.pricing.smsEur).toBe(0.05);
    expect(u.consumedPct.smsHour).toBe(1.67);
  });

  it("maps 429 to AmiRateLimitError and exposes policy", async () => {
    mock.on("POST", "/v1/agent/sms/send", () =>
      jsonResponse(429, { error: "monthly_budget_exceeded", mid: "mid_1" }),
    );
    try {
      await agent.sendSms({ to: "+34600", body: "x" });
      throw new Error("expected error");
    } catch (err) {
      expect(err).toBeInstanceOf(AmiRateLimitError);
      const e = err as AmiRateLimitError;
      expect(e.reason).toBe("monthly_budget_exceeded");
      expect(e.policy).toBe("monthly_budget_exceeded");
      expect(e.statusCode).toBe(429);
    }
  });

  it("wraps transport failures as AmiTransportError", async () => {
    mock.on("GET", "/v1/agent/self", () => {
      throw new Error("ECONNREFUSED");
    });
    await expect(agent.self()).rejects.toBeInstanceOf(AmiTransportError);
  });

  it("respects an AbortSignal from the caller", async () => {
    mock.on("GET", "/v1/agent/self", async () => {
      // Never resolves; the controller below will fire abort.
      await new Promise((r) => setTimeout(r, 50));
      return jsonResponse(200, {});
    });
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 10);
    await expect(agent.self(ctrl.signal)).rejects.toBeInstanceOf(AmiTransportError);
  });
});
