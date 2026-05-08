#!/usr/bin/env python3
"""Demo end-to-end del flujo AMI v1 contra el mock API.

Sin dependencias externas: usa solo la stdlib (urllib).
Variables de entorno:
    AMI_API_URL    URL base del backend AMI (default: http://localhost:8000)
    AMI_API_KEY    Bearer token; si no se setea, no se manda Authorization
"""
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("AMI_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("AMI_API_KEY") or None


def _headers(extra=None):
    h = {"Accept": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    if extra:
        h.update(extra)
    return h


def _request(method, path, payload=None, parse_json=True):
    url = API + path
    data = None
    headers = _headers()
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


def get(path):
    return _request("GET", path)


def post(path, payload=None):
    return _request("POST", path, payload or {})


def pp(title, obj):
    print("\n##", title)
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    pp("0 Health", get("/v1/health"))

    res = post("/v1/sim-requests", {
        "country": "ES",
        "sim_type": "eSIM",
        "capabilities": ["sms", "voice"],
        "agent": {"name": "Demerzel", "purpose": "agent_identity"},
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

    pp("7 Estado final de la SIMRequest", get(f"/v1/sim-requests/{sim_id}"))

    events = get("/v1/events")
    pp("8 Últimos AuditEvents", events)


if __name__ == "__main__":
    main()
