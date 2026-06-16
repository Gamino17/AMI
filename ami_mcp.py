#!/usr/bin/env python3
"""AMI MCP server (SDK oficial). Soporta stdio y HTTP streamable.

Uso:
    python ami_mcp.py                # stdio (Claude Desktop / Code / cliente local)
    python ami_mcp.py http           # streamable-http en :8001 (servidor remoto)

Variables de entorno:
    AMI_API_URL    URL base del backend AMI (default: http://localhost:8000)
    AMI_API_KEY    Bearer token enviado a la API (opcional en modo dev)
    AMI_MCP_HOST   Bind para HTTP (default: 0.0.0.0)
    AMI_MCP_PORT   Puerto para HTTP (default: 8001)
"""
from __future__ import annotations
import json, os, sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("AMI_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("AMI_API_KEY") or None
# Operations v2: agent_token scoped por MobileIdentity. Las tools de SMS/voz
# usan este token (no la API key del customer). Las tools que aceptan un
# agent_token explícito como argumento lo prefieren; si llega vacío, caen al
# AMI_AGENT_TOKEN del entorno.
DEFAULT_AGENT_TOKEN = os.environ.get("AMI_AGENT_TOKEN") or None


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _agent_headers(agent_token: str | None) -> dict[str, str]:
    """Headers para los endpoints scoped por agent_token."""
    token = agent_token or DEFAULT_AGENT_TOKEN
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(API_URL + path, headers=_headers())
        return {"http_status": r.status_code, "body": _safe_json(r)}


async def _post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(API_URL + path, json=body or {}, headers=_headers())
        return {"http_status": r.status_code, "body": _safe_json(r)}


async def _agent_get(path: str, agent_token: str | None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(API_URL + path, headers=_agent_headers(agent_token))
        return {"http_status": r.status_code, "body": _safe_json(r)}


async def _agent_post(path: str, body: dict[str, Any], agent_token: str | None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(API_URL + path, json=body, headers=_agent_headers(agent_token))
        return {"http_status": r.status_code, "body": _safe_json(r)}


def _safe_json(r: httpx.Response) -> Any:
    try: return r.json()
    except Exception: return {"raw": r.text}


mcp = FastMCP(
    "ami",
    instructions=(
        "AMI v1 — Agent Mobile Identity Protocol. Permite a un agente solicitar, "
        "contratar y activar una identidad móvil (SIM/eSIM/número). Flujo: "
        "request_sim_offer → accept_offer → submit_customer_data → initiate_kyc "
        "→ (el rep. legal verifica su identidad en verification_url) → create_contract "
        "→ (firmar en signature_url) → activate_sim_identity. El paso KYC es "
        "obligatorio antes del contrato: si create_contract responde "
        "'kyc_required'/'kyc_not_verified', llama a initiate_kyc y sondea "
        "get_kyc_status hasta 'verified'. La SIM física es lo único simulado; el "
        "resto del flujo es real."
    ),
)


@mcp.tool(name="ami.search_sim_options",
          description="Lista países, tipos de SIM/eSIM y capacidades disponibles.")
async def search_sim_options() -> dict:
    return await _get("/v1/sim-options")


@mcp.tool(name="ami.request_sim_offer",
          description="Crea una SIMRequest y devuelve la oferta inmediata del partner telco.")
async def request_sim_offer(
    agent_name: str,
    country: str = "ES",
    sim_type: str = "eSIM",
    capabilities: list[str] | None = None,
    purpose: str = "agent_identity",
    max_monthly_price: float | None = None,
    currency: str = "EUR",
) -> dict:
    return await _post("/v1/sim-requests", {
        "country": country,
        "sim_type": sim_type,
        "capabilities": capabilities or ["sms", "voice"],
        "agent": {"name": agent_name, "purpose": purpose},
        "commercial_constraints": {
            "max_monthly_price": max_monthly_price,
            "currency": currency,
        },
    })


@mcp.tool(name="ami.accept_offer", description="Acepta una oferta antes de generar contrato.")
async def accept_offer(offer_id: str) -> dict:
    return await _post(f"/v1/offers/{offer_id}/accept")


@mcp.tool(name="ami.submit_customer_data",
          description="Envía los datos legales/fiscales del cliente y los vincula a la SIMRequest.")
async def submit_customer_data(
    sim_request_id: str,
    legal_name: str,
    tax_id: str,
    billing_email: str,
    address: str,
    representative_name: str,
) -> dict:
    return await _post(f"/v1/sim-requests/{sim_request_id}/customer-data", {
        "customer": {
            "legal_name": legal_name,
            "tax_id": tax_id,
            "billing_email": billing_email,
            "address": address,
            "representative_name": representative_name,
        }
    })


@mcp.tool(name="ami.initiate_kyc",
          description="Dispara la verificación de identidad (KYC) del representante "
                      "legal del cliente. Paso obligatorio entre submit_customer_data "
                      "y create_contract cuando el backend exige KYC. Devuelve "
                      "verification_url: la página pública donde el humano sube su "
                      "documento + selfie. Pásasela al cliente por email/SMS/WhatsApp. "
                      "Idempotente: si ya hay un KYC en curso para la sim_request, "
                      "devuelve el existente.")
async def initiate_kyc(sim_request_id: str) -> dict:
    return await _post(f"/v1/sim-requests/{sim_request_id}/kyc/initiate")


@mcp.tool(name="ami.get_kyc_status",
          description="Consulta el estado del KYC vinculado a una sim_request: "
                      "pending (esperando que el humano suba docs) · submitted/in_review "
                      "(en revisión) · verified (ya se puede crear el contrato) · "
                      "rejected · expired. Úsala para sondear antes de reintentar "
                      "create_contract.")
async def get_kyc_status(sim_request_id: str) -> dict:
    return await _get(f"/v1/sim-requests/{sim_request_id}/kyc")


@mcp.tool(name="ami.create_contract",
          description="Genera el contrato vinculado a una oferta y un cliente. "
                      "Devuelve signature_url donde el firmante debe aceptar. "
                      "Requiere KYC verified si el backend lo exige (ver initiate_kyc).")
async def create_contract(offer_id: str, customer_id: str) -> dict:
    return await _post("/v1/contracts", {"offer_id": offer_id, "customer_id": customer_id})


@mcp.tool(name="ami.get_contract_status", description="Consulta el estado actual de un contrato.")
async def get_contract_status(contract_id: str) -> dict:
    return await _get(f"/v1/contracts/{contract_id}")


@mcp.tool(name="ami.confirm_signature_status",
          description="Comprueba si el contrato ya está firmado (alias semántico de get_contract_status).")
async def confirm_signature_status(contract_id: str) -> dict:
    return await _get(f"/v1/contracts/{contract_id}")


@mcp.tool(name="ami.activate_sim_identity",
          description="Tras la firma, inicia el provisioning con el partner telco. "
                      "Devuelve la MobileIdentity activa con phone_number y QR de eSIM.")
async def activate_sim_identity(contract_id: str) -> dict:
    return await _post("/v1/mobile-identities/activate", {"contract_id": contract_id})


@mcp.tool(name="ami.get_identity_status",
          description="Consulta el estado de una MobileIdentity activa.")
async def get_identity_status(mobile_identity_id: str) -> dict:
    return await _get(f"/v1/mobile-identities/{mobile_identity_id}")


@mcp.tool(name="ami.cancel_request",
          description="Cancela una SIMRequest antes de la activación.")
async def cancel_request(sim_request_id: str, reason: str = "cancelled_by_agent") -> dict:
    return await _post(f"/v1/sim-requests/{sim_request_id}/cancel", {"reason": reason})


@mcp.tool(name="ami.list_events",
          description="Devuelve los últimos AuditEvents del backend (debug/inspección).")
async def list_events() -> dict:
    return await _get("/v1/events")


@mcp.tool(name="ami.send_sms",
          description="Envía un SMS desde una MobileIdentity activa. Requiere agent_token "
                      "scoped (Nivel 2). Devuelve el id del mensaje en estado 'queued'; "
                      "el DLR lo lleva a 'sent' y 'delivered' después.")
async def send_sms(to: str, body: str, agent_token: str | None = None) -> dict:
    return await _agent_post("/v1/agent/sms/send", {"to": to, "body": body}, agent_token)


@mcp.tool(name="ami.list_sms",
          description="Lista los SMS de la MobileIdentity del agent_token (más reciente primero). "
                      "Opcional: filtrar por dirección 'outbound' o 'inbound'.")
async def list_sms(agent_token: str | None = None, limit: int = 20,
                   direction: str | None = None) -> dict:
    qs = f"?limit={int(limit)}"
    if direction in ("outbound", "inbound"):
        qs += f"&direction={direction}"
    return await _agent_get("/v1/agent/sms" + qs, agent_token)


@mcp.tool(name="ami.place_call",
          description="Origina una llamada saliente desde la MobileIdentity activa. "
                      "AMI marca al PSTN y bridgea por SIP al callback_sip_uri "
                      "(típicamente el endpoint SIP del cliente, p.ej. su URI Realtime "
                      "de OpenAI). AMI NO ejecuta voz: sólo cursa la pipa.")
async def place_call(to: str, callback_sip_uri: str,
                     agent_token: str | None = None) -> dict:
    return await _agent_post(
        "/v1/agent/calls/place",
        {"to": to, "callback_sip_uri": callback_sip_uri},
        agent_token,
    )


@mcp.tool(name="ami.list_calls",
          description="Lista las llamadas del MID asociado al agent_token, más reciente "
                      "primero. Opcional: filtrar por dirección 'outbound' o 'inbound'.")
async def list_calls(agent_token: str | None = None, limit: int = 20,
                     direction: str | None = None) -> dict:
    qs = f"?limit={int(limit)}"
    if direction in ("outbound", "inbound"):
        qs += f"&direction={direction}"
    return await _agent_get("/v1/agent/calls" + qs, agent_token)


@mcp.tool(name="ami.get_call",
          description="Detalle de una llamada (scoped al MID del agent_token).")
async def get_call(call_id: str, agent_token: str | None = None) -> dict:
    return await _agent_get(f"/v1/agent/calls/{call_id}", agent_token)


@mcp.tool(name="ami.hangup_call",
          description="Termina una llamada en curso del MID asociado al agent_token. "
                      "Idempotente sobre llamadas ya terminadas.")
async def hangup_call(call_id: str, agent_token: str | None = None) -> dict:
    return await _agent_post(f"/v1/agent/calls/{call_id}/hangup", {}, agent_token)


@mcp.tool(name="ami.set_inbound_sip_uri",
          description="Configura el endpoint SIP al que el partner debe reenviar las "
                      "llamadas entrantes de un MID (típicamente el endpoint Realtime "
                      "del cliente). Pasa null para limpiar la config. Auth: API key del "
                      "customer (no agent_token).")
async def set_inbound_sip_uri(mobile_identity_id: str,
                              inbound_sip_uri: str | None) -> dict:
    return await _post(
        f"/v1/mobile-identities/{mobile_identity_id}/inbound-config",
        {"inbound_sip_uri": inbound_sip_uri},
    )


@mcp.tool(name="ami.create_webhook",
          description="Registra un webhook saliente para un MID. AMI hará POST al "
                      "url cuando ocurran eventos del MID. Devuelve el secret una "
                      "sola vez (úsalo para verificar X-Ami-Signature de cada entrega).")
async def create_webhook(mobile_identity_id: str, url: str,
                          events: list[str] | None = None) -> dict:
    return await _post(
        f"/v1/mobile-identities/{mobile_identity_id}/webhooks",
        {"url": url, "events": events or ["*"]},
    )


@mcp.tool(name="ami.list_webhooks",
          description="Lista los webhooks salientes registrados para un MID (sin "
                      "exponer el secret completo, sólo prefijo).")
async def list_webhooks(mobile_identity_id: str) -> dict:
    return await _get(f"/v1/mobile-identities/{mobile_identity_id}/webhooks")


@mcp.tool(name="ami.delete_webhook",
          description="Elimina un webhook saliente de un MID por su id.")
async def delete_webhook(mobile_identity_id: str, webhook_id: str) -> dict:
    return await _post(
        f"/v1/mobile-identities/{mobile_identity_id}/webhooks/{webhook_id}/delete"
    )


@mcp.tool(name="ami.get_limits",
          description="Lee los límites (rate + budget + countries) configurados "
                      "para un MID. Auth: API key del customer.")
async def get_limits(mobile_identity_id: str) -> dict:
    return await _get(f"/v1/mobile-identities/{mobile_identity_id}/limits")


@mcp.tool(name="ami.update_limits",
          description="Actualiza los límites del MID (patch parcial). Acepta "
                      "sms_per_hour, sms_per_day, calls_per_hour, calls_per_day, "
                      "monthly_budget_eur, allowed_country_prefixes (lista de '+34', etc.).")
async def update_limits(mobile_identity_id: str,
                         sms_per_hour: int | None = None,
                         sms_per_day: int | None = None,
                         calls_per_hour: int | None = None,
                         calls_per_day: int | None = None,
                         monthly_budget_eur: float | None = None,
                         allowed_country_prefixes: list[str] | None = None) -> dict:
    payload: dict[str, Any] = {}
    if sms_per_hour is not None: payload["sms_per_hour"] = sms_per_hour
    if sms_per_day is not None: payload["sms_per_day"] = sms_per_day
    if calls_per_hour is not None: payload["calls_per_hour"] = calls_per_hour
    if calls_per_day is not None: payload["calls_per_day"] = calls_per_day
    if monthly_budget_eur is not None: payload["monthly_budget_eur"] = monthly_budget_eur
    if allowed_country_prefixes is not None: payload["allowed_country_prefixes"] = allowed_country_prefixes
    return await _post(f"/v1/mobile-identities/{mobile_identity_id}/limits", payload)


@mcp.tool(name="ami.get_usage",
          description="Lee el usage actual del MID (SMS hoy, llamadas hoy, gasto este "
                      "mes, % consumido por tope). Auth: API key del customer.")
async def get_usage(mobile_identity_id: str) -> dict:
    return await _get(f"/v1/mobile-identities/{mobile_identity_id}/usage")


@mcp.tool(name="ami.get_my_usage",
          description="Como get_usage pero scoped al MID del agent_token. Útil para "
                      "que el agente compruebe cuánto le queda antes de pedir más SMS "
                      "o llamadas.")
async def get_my_usage(agent_token: str | None = None) -> dict:
    return await _agent_get("/v1/agent/usage", agent_token)


@mcp.tool(name="ami.rotate_agent_token",
          description="Rota el agent_token de una MobileIdentity activa. "
                      "Invalida el token anterior al instante (hard rotate) y devuelve "
                      "uno nuevo en plano. Pensado para usarse desde el cliente owner "
                      "cuando se sospecha filtración del token.")
async def rotate_agent_token(mobile_identity_id: str) -> dict:
    return await _post(f"/v1/mobile-identities/{mobile_identity_id}/rotate-token")


def main() -> None:
    transport = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AMI_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "http":
        # FastMCP activa DNS-rebinding protection por defecto y solo permite
        # hosts/origenes localhost. En producción detrás de un proxy hay que
        # añadir TODOS los hostnames públicos a allowed_hosts. Aceptamos:
        #   - RENDER_EXTERNAL_HOSTNAME (Render lo expone, p.ej. ami-mcp-http.onrender.com)
        #   - AMI_MCP_PUBLIC_HOST: lista separada por comas con dominios custom
        #     (p.ej. mcp.protocolami.com,otrodominio.com)
        # Sin esto, el server responde 421 "Invalid Host header" al proxy.
        hosts_to_allow = []
        render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if render_host:
            hosts_to_allow.append(render_host)
        custom = os.environ.get("AMI_MCP_PUBLIC_HOST", "")
        for h in custom.split(","):
            h = h.strip()
            if h:
                hosts_to_allow.append(h)

        if hosts_to_allow:
            ts = mcp.settings.transport_security
            extra_hosts, extra_origins = [], []
            for h in hosts_to_allow:
                extra_hosts += [h, f"{h}:*"]
                extra_origins += [f"https://{h}", f"http://{h}"]
            ts.allowed_hosts = list(ts.allowed_hosts) + extra_hosts
            ts.allowed_origins = list(ts.allowed_origins) + extra_origins

        # Stateless: cada llamada es independiente. Imprescindible cuando el
        # server puede correr en múltiples instancias o reciclar workers
        # (Render free/starter), porque la sesión por defecto se guarda en
        # memoria del proceso y se perdería entre requests dando "Session
        # terminated".
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True

        import uvicorn
        from starlette.responses import HTMLResponse
        from starlette.routing import Route

        host = os.environ.get("AMI_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("AMI_MCP_PORT", "8001"))
        landing_url = os.environ.get("AMI_API_URL", "https://protocolami.com")

        # Página HTML amable para humanos que llegan al MCP server por curiosidad.
        # Servida en GET / (raíz) y como fallback para GET /mcp con Accept: text/html.
        page = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>AMI MCP Server</title>"
            "<style>"
            "body{font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif;"
            "background:#08080c;color:#ededf2;display:flex;align-items:center;justify-content:center;"
            "min-height:100vh;margin:0;padding:1.5rem;line-height:1.6}"
            ".box{max-width:560px;text-align:center}"
            "h1{font-weight:700;font-size:2rem;margin:0 0 1rem;letter-spacing:-0.02em}"
            "h1 .dot{color:#8b6cff}"
            "p{color:#8888a0;margin:0 0 1.5rem}"
            "code{font-family:'JetBrains Mono','SF Mono',Menlo,monospace;background:#14141d;"
            "border:1px solid #1f1f2c;padding:0.15em 0.5em;border-radius:4px;color:#5dd1ff;font-size:0.9em}"
            ".pill{display:inline-block;font-family:'JetBrains Mono','SF Mono',Menlo,monospace;"
            "font-size:0.72rem;background:#14141d;border:1px solid #1f1f2c;color:#8888a0;"
            "padding:0.3rem 0.7rem;border-radius:999px;margin-bottom:1.5rem;letter-spacing:0.05em}"
            ".btn{display:inline-block;background:linear-gradient(180deg,#9d80ff,#7a5cff);"
            "color:#fff;text-decoration:none;padding:0.7rem 1.4rem;border-radius:8px;"
            "font-weight:500;margin-top:1rem;box-shadow:0 8px 24px -8px rgba(123,92,255,0.5)}"
            "</style></head><body><div class=\"box\">"
            "<div class=\"pill\">MCP HTTP server</div>"
            "<h1>AMI<span class=\"dot\">.</span></h1>"
            "<p>This URL is the <strong>streamable-http MCP endpoint</strong> for AI agents, "
            "not a website. Point any MCP-compatible client at <code>/mcp</code> "
            "and it will see the <code>ami.*</code> tools (contract, SMS, voice, governance).</p>"
            "<p>If you arrived here from a browser, you probably want the AMI landing page:</p>"
            f"<a class=\"btn\" href=\"{landing_url}\">Go to ami-mock-api &rarr;</a>"
            "</div></body></html>"
        )

        async def root(request):
            return HTMLResponse(page)

        async def mcp_browser(request):
            # GET /mcp con Accept: text/html → landing amable; otros casos → 405
            # (los POSTs reales del MCP los maneja el handler de FastMCP montado abajo).
            if "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(page)
            return HTMLResponse(
                "Method Not Allowed (use POST with Accept: application/json, text/event-stream)",
                status_code=405,
            )

        app = mcp.streamable_http_app()
        # Inserta las rutas amables al principio para que tengan prioridad sobre
        # cualquier 404 catch-all del app de FastMCP.
        app.routes.insert(0, Route("/", root, methods=["GET"]))
        app.routes.insert(1, Route("/mcp", mcp_browser, methods=["GET"]))

        uvicorn.run(
            app, host=host, port=port,
            proxy_headers=True, forwarded_allow_ips="*",
            log_level="info",
        )
    else:
        print(f"Unknown transport: {transport!r}. Use 'stdio' or 'http'.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
