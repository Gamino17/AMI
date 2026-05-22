#!/usr/bin/env python3
"""Receta minimal de un agente que usa AMI.

Pensado como ejemplo copy-paste para devs que quieren integrar un agente AI
contra AMI sin pasar por MCP — solo REST. Cubre los dos planos del protocolo:

  1. ONE-TIME (al alta del agente o cuando se necesita un nuevo número):
     contratar → firmar → activar → guardar el agent_token devuelto.

  2. RUNTIME (cada vez que el agente quiere enviar SMS o hacer una llamada):
     usar el agent_token guardado contra los endpoints /v1/agent/*.

Sin dependencias externas. Stdlib pura.

Uso:
    export AMI_API_URL="https://api.protocolami.com"
    export AMI_API_KEY="<tu API key de customer>"
    python examples/agent_quickstart.py
"""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request


API = os.environ.get("AMI_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("AMI_API_KEY")
if not API_KEY:
    raise SystemExit("Falta AMI_API_KEY (la API key del customer).")


def _http(method: str, path: str, body: dict | None = None,
          bearer: str | None = None) -> dict:
    """Helper HTTP que devuelve JSON. Auth Bearer con API_KEY del customer
    salvo que se pase otro `bearer` explícito (ej. agent_token)."""
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = bearer if bearer is not None else API_KEY
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code} {method} {path}: {detail[:200]}")


# ============================================================================
# 1. ONE-TIME · contratar un número y obtener el agent_token
# ============================================================================
def provision_number(customer_data: dict, country: str = "ES") -> dict:
    """Recorre todo el ciclo de contratación y devuelve {mid, phone, agent_token}.

    Llamar UNA sola vez por número. Persistir agent_token en el almacén seguro
    del agente (vault, secret manager, KMS...). Si se filtra, rotarlo con
    POST /v1/mobile-identities/{mid}/rotate-token.
    """
    # 1.1 Solicitud + oferta inmediata
    r = _http("POST", "/v1/sim-requests", {
        "country": country,
        "sim_type": "eSIM",
        "capabilities": ["sms", "voice"],
        "agent": {"name": "agent01", "purpose": "agent_identity"},
    })
    sim_id = r["sim_request"]["id"]
    offer_id = r["offer"]["id"]

    # 1.2 Aceptar oferta + datos del cliente
    _http("POST", f"/v1/offers/{offer_id}/accept")
    cust = _http("POST", f"/v1/sim-requests/{sim_id}/customer-data",
                 {"customer": customer_data})
    customer_id = cust["customer"]["id"]

    # 1.3 Contrato + firma (mock-sign para automatización; en prod el firmante
    # abriría signature_url en el navegador)
    c = _http("POST", "/v1/contracts",
              {"offer_id": offer_id, "customer_id": customer_id})
    _http("POST", f"/v1/contracts/{c['id']}/mock-sign")

    # 1.4 Activación: devuelve el agent_token en plano una sola vez
    identity = _http("POST", "/v1/mobile-identities/activate",
                     {"contract_id": c["id"]})
    return {
        "mid": identity["id"],
        "phone": identity["phone_number"],
        "agent_token": identity["agent_token"],
    }


# ============================================================================
# 2. RUNTIME · usar el agent_token para operar
# ============================================================================
def send_sms(agent_token: str, to: str, body: str) -> dict:
    """Envía un SMS desde el MID dueño del agent_token."""
    return _http("POST", "/v1/agent/sms/send",
                 {"to": to, "body": body}, bearer=agent_token)


def list_sms(agent_token: str, limit: int = 20) -> list[dict]:
    return _http("GET", f"/v1/agent/sms?limit={limit}",
                 bearer=agent_token)["messages"]


def place_call(agent_token: str, to: str, callback_sip_uri: str) -> dict:
    """Origina una llamada. callback_sip_uri es a dónde AMI bridgea cuando
    el destinatario contesta (típicamente el endpoint SIP de tu motor de voz
    en tiempo real, o tu PBX). AMI no ejecuta voz — sólo cursa la pipa."""
    return _http("POST", "/v1/agent/calls/place",
                 {"to": to, "callback_sip_uri": callback_sip_uri},
                 bearer=agent_token)


def get_usage(agent_token: str) -> dict:
    """Vista del consumo del MID + % consumido por tope. Útil para que el
    agente decida si seguir gastando o avisar al humano."""
    return _http("GET", "/v1/agent/usage", bearer=agent_token)


# ============================================================================
# Demo
# ============================================================================
def main() -> None:
    print(">>> Provisioning new number...")
    creds = provision_number({
        "legal_name": "Acme Robotics S.L.",
        "tax_id": "B12345678",
        "billing_email": "billing@acme.test",
        "address": "Calle Falsa 123, 28001 Madrid",
        "representative_name": "Ada Lovelace",
    })
    print(json.dumps(creds, indent=2))
    print(f"\n>>> El agente tiene un número: {creds['phone']}")
    print(f">>> Guarda este agent_token en tu vault: {creds['agent_token'][:20]}...")

    print("\n>>> Enviando un SMS desde el agente...")
    msg = send_sms(creds["agent_token"], "+34 600 111 222",
                   "Hola — recordatorio: dentista mañana 10:00")
    print(f"    SMS encolado: {msg['id']} ({msg['status']})")

    # Damos tiempo al adapter (mock) a procesar el DLR. En live el DLR llega
    # cuando el partner lo confirma, típicamente en segundos.
    for _ in range(20):
        time.sleep(0.1)
        m = next((m for m in list_sms(creds["agent_token"]) if m["id"] == msg["id"]), None)
        if m and m["status"] in ("delivered", "failed"):
            break
    print(f"    Estado final: {m['status']}")

    print("\n>>> Originando una llamada (bridge-by-API)...")
    call = place_call(creds["agent_token"], "+34 600 999 888",
                      "sip:agent@cliente.example;transport=tls")
    print(f"    Llamada {call['id']} en estado {call['status']}")

    print("\n>>> Usage del MID:")
    u = get_usage(creds["agent_token"])
    print(f"    SMS hoy: {u['usage']['sms_count_today']} / {u['limits']['sms_per_day']}")
    print(f"    Llamadas hoy: {u['usage']['calls_count_today']} / {u['limits']['calls_per_day']}")
    print(f"    Gasto este mes: {u['usage']['spend_this_month_eur']} EUR "
          f"/ {u['limits']['monthly_budget_eur']} EUR")


if __name__ == "__main__":
    main()
