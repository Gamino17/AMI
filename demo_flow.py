#!/usr/bin/env python3
"""Demo end-to-end del flujo AMI: contratación (v1) + operación (v2).

Recorre todo el ciclo de vida contra el backend mock:

  Plano contratación (auth: AMI_API_KEY del customer):
    1. SIMRequest + oferta inmediata
    2. Aceptar oferta + datos del cliente
    3. Contrato + firma + activación de MobileIdentity
    4. Devuelve agent_token scoped al MID (Nivel 2)

  Plano operación (auth: agent_token):
    5. Enviar SMS (sale 'queued', el mock lo lleva a 'delivered')
    6. Configurar inbound_sip_uri del MID + simular llamada entrante
    7. Originar llamada saliente (bridge-by-API)
    8. Consultar usage (rate limits + spending del MID)
    9. Crear webhook saliente y simular el dispatch

Sin dependencias externas — sólo stdlib (urllib).
Variables de entorno:
    AMI_API_URL    URL base del backend AMI (default: http://localhost:8000)
    AMI_API_KEY    Bearer token; si no se setea, no se manda Authorization
    AMI_TELCO_INBOUND_KEY   Si está, simula también el webhook del partner
                            (sms entrante + call entrante). Opcional.
"""
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("AMI_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("AMI_API_KEY") or None
TELCO_KEY = os.environ.get("AMI_TELCO_INBOUND_KEY") or None


def _headers(extra=None, bearer=None):
    h = {"Accept": "application/json"}
    token = bearer if bearer is not None else API_KEY
    if token:
        h["Authorization"] = f"Bearer {token}"
    if extra:
        h.update(extra)
    return h


def _request(method, path, payload=None, parse_json=True, bearer=None, extra_headers=None):
    url = API + path
    data = None
    headers = _headers(extra=extra_headers, bearer=bearer)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            if not parse_json:
                return {"http_status": resp.status}
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except Exception:
            detail = {"raw": body[:200]}
        raise SystemExit(f"HTTP {e.code} en {method} {path}: {json.dumps(detail, ensure_ascii=False)}")


def get(path, bearer=None):
    return _request("GET", path, bearer=bearer)


def post(path, payload=None, bearer=None, extra_headers=None):
    return _request("POST", path, payload or {}, bearer=bearer, extra_headers=extra_headers)


def pp(title, obj):
    print("\n##", title)
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    pp("0 Health", get("/v1/health"))

    res = post("/v1/sim-requests", {
        "country": "ES",
        "sim_type": "eSIM",
        "capabilities": ["sms", "voice"],
        "agent": {"name": "Agente01", "purpose": "agent_identity"},
        "commercial_constraints": {"max_monthly_price": 10, "currency": "EUR"},
    })
    pp("1 SIMRequest + oferta inmediata", res)
    sim_id = res["sim_request"]["id"]
    offer_id = res["offer"]["id"]

    pp("2 Aceptar oferta", post(f"/v1/offers/{offer_id}/accept"))

    customer_payload = {
        "customer": {
            "legal_name": "Parallax IEI S.L.",
            "tax_id": "B00000000",
            "billing_email": "demo@parallax.ai",
            "address": "Madrid, España",
            "representative_name": "Daniel Gamino",
        }
    }
    res_customer = post(f"/v1/sim-requests/{sim_id}/customer-data", customer_payload)
    pp("3 Datos de cliente vinculados a la SIMRequest", res_customer)
    customer_id = res_customer["customer"]["id"]

    contract = post("/v1/contracts", {"offer_id": offer_id, "customer_id": customer_id})
    pp("4 Contrato creado (pendiente de firma)", contract)
    contract_id = contract["id"]
    signature_url = contract["signature_url"]

    print("\n## 5 Firma del contrato")
    print(f"   En producción el firmante abriría esta URL en su navegador:")
    print(f"     {signature_url}")
    print("   Aquí simulamos el clic del botón 'Firmar contrato' (POST al callback público).")
    sign_path = signature_url.split(API, 1)[-1] if signature_url.startswith(API) else f"/v1/sign/{contract_id}"
    # Callback público (no requiere API key, devuelve HTML).
    _request("POST", f"{sign_path}/confirm", parse_json=False)
    pp("   Estado del contrato tras firmar", get(f"/v1/contracts/{contract_id}"))

    identity = post("/v1/mobile-identities/activate", {"contract_id": contract_id})
    pp("6 MobileIdentity activa", identity)
    mid = identity["id"]
    agent_token = identity["agent_token"]
    phone = identity["phone_number"]

    # ===================== OPERATIONS V2 =====================
    # A partir de aquí usamos el agent_token (Nivel 2), NO la AMI_API_KEY.
    print("\n=== Operations v2 · scoped por agent_token ===")

    # Self-check del agente
    pp("7 GET /v1/agent/self (auth: agent_token)",
       get("/v1/agent/self", bearer=agent_token))

    # SMS saliente
    sms_out = post("/v1/agent/sms/send",
                   {"to": "+34 600 111 222", "body": "Hola desde el agente"},
                   bearer=agent_token)
    pp("8 SMS saliente · queued (el mock lo lleva a delivered)", sms_out)

    # Pausa breve para que el mock simule el DLR
    import time
    time.sleep(0.7)
    pp("9 Listado SMS del MID (debe verse 'delivered')",
       get("/v1/agent/sms", bearer=agent_token))

    # SMS entrante simulado (sólo si el partner-key está disponible)
    if TELCO_KEY:
        inbound = _request("POST", "/v1/_telco/sms/inbound",
                           payload={"from": "+34611000000", "to": phone,
                                    "body": "Respuesta del destinatario"},
                           extra_headers={"X-Telco-Key": TELCO_KEY},
                           bearer="")  # explícito: sin Authorization customer
        pp("10 SMS entrante simulado (webhook del partner)", inbound)

    # ===================== VOZ — bridge-by-API =====================
    print("\n=== Operations v2 · voz (bridge-by-API) ===")

    # Configurar inbound del MID (a dónde reenviar llamadas entrantes)
    inbound_cfg = post(f"/v1/mobile-identities/{mid}/inbound-config",
                       {"inbound_sip_uri": "sip:agent@cliente.example;transport=tls"})
    pp("11 inbound_sip_uri configurado", inbound_cfg)

    # Llamada saliente bridge-by-API
    call = post("/v1/agent/calls/place",
                {"to": "+34 600 999 888",
                 "callback_sip_uri": "sip:agent@cliente.example;transport=tls"},
                bearer=agent_token)
    pp("12 Llamada saliente · initiated", call)

    # Esperar a que el mock simule ringing/answer/hangup
    time.sleep(1.2)
    pp("13 Estado final de la llamada (completed con duration_sec)",
       get(f"/v1/agent/calls/{call['id']}", bearer=agent_token))

    # ===================== GOVERNANCE =====================
    print("\n=== Governance · limits + usage + webhooks ===")

    pp("14 Usage del MID (con tarifas y % consumido)",
       get("/v1/agent/usage", bearer=agent_token))

    pp("15 Ajustar límite diario de SMS a 1000",
       post(f"/v1/mobile-identities/{mid}/limits", {"sms_per_day": 1000}))

    pp("16 Crear webhook saliente (devuelve secret una sola vez)",
       post(f"/v1/mobile-identities/{mid}/webhooks",
            {"url": "https://customer.example.com/ami/hook",
             "events": ["sms.inbound", "call.completed"]}))

    pp("17 SIMRequest final + Audit log",
       {"sim_request": get(f"/v1/sim-requests/{sim_id}"),
        "audit_events_count": len(get("/v1/events")["events"])})


if __name__ == "__main__":
    main()
